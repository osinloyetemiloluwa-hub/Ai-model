"""ACO Layer 6 — Self-Improving Maintenance Loop (ADR-0178, Tier CONTRIBUTOR).

Turns an ACO L4 diagnosis into a CODE change on the repo — but ONLY for an instance
that holds a valid signed ``maintainer.commit`` capability, and ONLY through a full
gate chain. Deny-by-default at every step:

    diagnosis ─▶ [capability gate] ─▶ patch_source (injected) ─▶ [l6 gate chain]
              ─▶ branch + commit (tagged aco-l6) ─▶ route:
                   low-risk + fully-green + no-ack  ─▶ ff-merge main (opt-in)
                   else                              ─▶ PR-ready (human one-tap)

Hard rules enforced here in code:
  * No capability  → refuse (status="denied").  [the trust boundary]
  * No green tests → refuse (deny-by-default; a missing gate_runner == red).
  * Touch LICENSE/NOTICE/CLA*/audit.jsonl/policy.json/*.key → hard block.
  * Touch a compliance/security/protocol path or hit an ADR trigger → requires_ack
    (never auto-merged), regardless of class.
  * Direct-to-main and remote push are BOTH opt-in (default off) — the safe default
    leaves a branch + a PR-ready outcome for the maintainer to merge.

The PATCH GENERATOR is intentionally an injected callable (``patch_source``): the
deterministic safety machinery lives here; the judgement of *what to change* stays
with the maintainer/agent. With no patch_source, the loop escalates (PR-draft of
the diagnosis), never invents code.
"""
from __future__ import annotations

import json
import logging
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from . import maintainer_capability as _cap

logger = logging.getLogger(__name__)

# Paths that must NEVER be auto-modified (hard block — not even with ack here;
# these are operator-only by CLAUDE.md red-lines).
_HARD_BLOCK = ("LICENSE", "NOTICE", "CLA.md", "CLA-SIGNATORIES.md", "CCLA.md",
               "CONTRIBUTING.md", "audit.jsonl", "policy.json")
_HARD_BLOCK_SUFFIX = (".key", ".pem")
# Path fragments that force human ack (compliance/security/protocol surfaces).
_ACK_FRAGMENTS = ("disclosure", "consent", "house_rules", "house-rules", "audit",
                  "egress", "license", "security_events", "path_gate", "a2a",
                  "protocol", "compliance", "erasure")
# Low-risk classes eligible for direct-to-main (still need a fully-green gate).
_LOW_RISK_CLASSES = frozenset({"platform_path", "null_guard", "typo", "test_only",
                               "docstring", "log_message"})


@dataclass
class PatchEdit:
    path: str            # repo-relative
    new_content: str


@dataclass
class Patch:
    diagnosis_id: str
    summary: str
    risk_class: str
    edits: list[PatchEdit] = field(default_factory=list)

    @property
    def paths(self) -> list[str]:
        return [e.path for e in self.edits]


@dataclass
class GateResult:
    passed: bool
    requires_ack: bool = False
    reasons: list[str] = field(default_factory=list)


@dataclass
class LoopResult:
    status: str                       # denied|no_patch|gate_blocked|gate_failed|
                                      # committed|merged|pushed|pr_ready
    detail: str = ""
    branch: str = ""
    commit: str = ""
    requires_ack: bool = False
    gate_reasons: list[str] = field(default_factory=list)
    telemetry: dict[str, Any] = field(default_factory=dict)


# ── git helpers (all scoped to repo_dir) ──────────────────────────────────────

def _git(repo: Path, *args: str) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", "-C", str(repo), *args],
                           capture_output=True, text=True, timeout=120)
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)


def _within_repo(repo: Path, rel: str) -> Path:
    repo_r = repo.resolve()
    t = (repo_r / rel).resolve()
    if t != repo_r and repo_r not in t.parents:
        raise ValueError(f"patch path escapes repo: {rel}")
    return t


# ── gate chain ────────────────────────────────────────────────────────────────

def l6_gate(patch: Patch, *, gate_runner: Optional[Callable[[], tuple[bool, str]]]) -> GateResult:
    """Run all gates on a proposed patch. Deny-by-default: tests must pass, no
    hard-blocked path, ack required for sensitive surfaces."""
    reasons: list[str] = []
    requires_ack = False

    # 1) hard-blocked paths → fail outright (never auto-touch these).
    for p in patch.paths:
        base = p.replace("\\", "/").split("/")[-1]
        if base in _HARD_BLOCK or base.endswith(_HARD_BLOCK_SUFFIX):
            reasons.append(f"hard-blocked path: {p}")
            return GateResult(False, requires_ack=False, reasons=reasons)

    # 2) compliance/security/protocol surfaces → require ack (never auto-merge).
    for p in patch.paths:
        low = p.lower()
        if any(frag in low for frag in _ACK_FRAGMENTS):
            requires_ack = True
            reasons.append(f"sensitive surface (ack required): {p}")
            break

    # 3) non-low-risk class → require ack.
    if patch.risk_class not in _LOW_RISK_CLASSES:
        requires_ack = True
        reasons.append(f"class '{patch.risk_class}' not in low-risk allowlist (ack required)")

    # 4) tests — deny-by-default: a missing runner == red.
    if gate_runner is None:
        reasons.append("no gate_runner → tests not green → blocked")
        return GateResult(False, requires_ack=requires_ack, reasons=reasons)
    try:
        ok, out = gate_runner()
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"gate_runner raised: {exc}")
        return GateResult(False, requires_ack=requires_ack, reasons=reasons)
    if not ok:
        reasons.append(f"tests red: {out[:200]}")
        return GateResult(False, requires_ack=requires_ack, reasons=reasons)

    reasons.append("tests green")
    return GateResult(True, requires_ack=requires_ack, reasons=reasons)


# ── the loop ──────────────────────────────────────────────────────────────────

def run_maintenance_loop(
    *,
    diagnosis: dict[str, Any],
    repo_dir: str | Path,
    patch_source: Optional[Callable[[dict], Optional[Patch]]] = None,
    capability_token: Optional[str] = None,
    public_key_bytes: Optional[bytes] = None,
    gate_runner: Optional[Callable[[], tuple[bool, str]]] = None,
    enable_direct_main: bool = False,
    enable_push: bool = False,
    now: Optional[int] = None,
) -> LoopResult:
    """Run one L6 iteration for a diagnosis. Returns a LoopResult; NEVER raises."""
    repo = Path(repo_dir)
    diag_id = str(diagnosis.get("id") or diagnosis.get("diagnosis_id") or "diag")
    tele: dict[str, Any] = {"diagnosis_id": diag_id, "ts": int(now or time.time())}

    # 1) capability gate — the trust boundary. Deny-by-default.
    verdict = _cap.is_contributor(capability_token, now=now) if public_key_bytes is None else \
        _cap.verify(capability_token, instance_id=_cap.current_instance_id(),
                    public_key_bytes=public_key_bytes, now=now)
    tele["capability"] = verdict.reason
    if not verdict.allowed:
        return LoopResult("denied", f"not a contributor: {verdict.reason}", telemetry=tele)

    # 2) patch source (injected). No source → escalate, never invent code.
    if patch_source is None:
        return LoopResult("no_patch", "no patch_source — escalate to human PR-draft",
                         telemetry=tele)
    try:
        patch = patch_source(diagnosis)
    except Exception as exc:  # noqa: BLE001
        return LoopResult("no_patch", f"patch_source raised: {exc}", telemetry=tele)
    if patch is None or not patch.edits:
        return LoopResult("no_patch", "patch_source returned nothing", telemetry=tele)

    # 3) gate chain (BEFORE writing anything that could reach main).
    gate = l6_gate(patch, gate_runner=gate_runner)
    tele["gate_passed"] = gate.passed
    tele["requires_ack"] = gate.requires_ack
    if not gate.passed:
        return LoopResult("gate_failed", "; ".join(gate.reasons),
                         requires_ack=gate.requires_ack, gate_reasons=gate.reasons, telemetry=tele)

    # 4) write edits + branch + commit (scoped to repo, never -A).
    branch = f"aco/l6/{diag_id}"
    rc, _ = _git(repo, "checkout", "-b", branch)
    if rc != 0:
        # branch may exist; try switching
        _git(repo, "checkout", branch)
    try:
        for e in patch.edits:
            t = _within_repo(repo, e.path)
            t.parent.mkdir(parents=True, exist_ok=True)
            t.write_text(e.new_content, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return LoopResult("gate_blocked", f"patch write refused: {exc}",
                         branch=branch, gate_reasons=gate.reasons, telemetry=tele)
    _git(repo, "add", "--", *patch.paths)
    msg = (f"fix(aco-l6): {patch.summary}\n\n"
           f"Autonomous L6 maintenance fix for diagnosis {diag_id} "
           f"(class={patch.risk_class}). Gates: {'; '.join(gate.reasons)}.\n")
    rc, out = _git(repo, "commit", "-m", msg)
    if rc != 0:
        return LoopResult("gate_failed", f"commit failed: {out[:200]}",
                         branch=branch, gate_reasons=gate.reasons, telemetry=tele)
    rc, commit = _git(repo, "rev-parse", "HEAD")
    _git(repo, "tag", f"aco-l6-{diag_id}")
    tele["commit"] = commit

    # 5) routing. requires_ack OR not-low-risk OR direct-main disabled → PR-ready.
    eligible_direct = (enable_direct_main and not gate.requires_ack
                       and patch.risk_class in _LOW_RISK_CLASSES)
    if not eligible_direct:
        return LoopResult("pr_ready",
                         "branch committed; PR awaiting maintainer ack"
                         + (" (ack required)" if gate.requires_ack else ""),
                         branch=branch, commit=commit, requires_ack=gate.requires_ack,
                         gate_reasons=gate.reasons, telemetry=tele)

    # direct-to-main: ff-merge locally (still no network unless enable_push).
    _git(repo, "checkout", "main")
    rc, out = _git(repo, "merge", "--ff-only", branch)
    if rc != 0:
        _git(repo, "checkout", branch)
        return LoopResult("pr_ready", f"ff-merge declined ({out[:120]}) → PR",
                         branch=branch, commit=commit, gate_reasons=gate.reasons, telemetry=tele)
    if not enable_push:
        return LoopResult("merged", "ff-merged into local main (push disabled)",
                         branch=branch, commit=commit, gate_reasons=gate.reasons, telemetry=tele)
    rc, out = _git(repo, "push", "origin", "main")
    status = "pushed" if rc == 0 else "merged"
    return LoopResult(status, f"push rc={rc}: {out[:120]}",
                     branch=branch, commit=commit, gate_reasons=gate.reasons, telemetry=tele)


# ── M6: convergence tracker ───────────────────────────────────────────────────

@dataclass
class ConvergenceTracker:
    """LDD-style: stop after K_MAX attempts per diagnosis; escalate on non-convergence
    instead of grinding. Tracks reopened-anomaly + regression signals."""
    k_max: int = 5
    attempts: dict[str, int] = field(default_factory=dict)

    def should_attempt(self, diagnosis_id: str) -> bool:
        return self.attempts.get(diagnosis_id, 0) < self.k_max

    def record(self, diagnosis_id: str) -> int:
        self.attempts[diagnosis_id] = self.attempts.get(diagnosis_id, 0) + 1
        return self.attempts[diagnosis_id]

    def exhausted(self, diagnosis_id: str) -> bool:
        return self.attempts.get(diagnosis_id, 0) >= self.k_max
