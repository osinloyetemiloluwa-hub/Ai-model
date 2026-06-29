"""ACO Layer 5 — Actuating Self-Repair (ADR-0178, Tier LOCAL).

Promotes L5 from log-only (repair.py) to BOUNDED, REVERSIBLE, ALLOWLISTED repair
of the runtime ENVIRONMENT — never the installed code. Every action:

  * is registered (closed registry — an unknown fault escalates, never improvises);
  * writes ONLY within CORVIN_HOME (asserted by _assert_within_home; the installed
    package / site-packages / repo are structurally immutable here — program code is
    updated only via pip);
  * is REVERSIBLE (records an undo) and LOSS-GATED (if applying it did not clear the
    fault, the action is rolled back);
  * is AUDITED (L16 hash chain, best-effort) + logged to <home>/aco_repair.jsonl;
  * performs ZERO network egress (private/offline — GDPR Art. 5/25, EU AI Act).

Tiering: ``risk="safe"`` actions run by default; ``risk="risky"`` need
``CORVIN_ACO_L5_RISKY=1``. Global kill switch: ``CORVIN_ACO_L5_OFF=1`` (back to
log-only). This is Tier LOCAL only — it NEVER commits code or reaches ``main``
(that is L6, ADR-0178, gated by a signed maintainer capability).
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

RISK_SAFE = "safe"
RISK_RISKY = "risky"


# ── Context + result types ────────────────────────────────────────────────────

@dataclass
class RepairContext:
    """Everything an action needs. ``corvin_home`` is the ONLY writable root."""
    corvin_home: Path
    tenant_id: str = "_default"
    now: float = 0.0  # epoch; 0 → resolved at run time (kept injectable for tests)

    def time(self) -> float:
        return self.now or time.time()


@dataclass
class RepairOutcome:
    action_id: str
    status: str            # applied | reverted | failed | skipped | would_apply
    detail: str = ""
    fixed: int = 0         # how many fault instances were cleared


class RepairScopeError(Exception):
    """Raised when an action tries to write outside CORVIN_HOME — fail-closed."""


# ── Path-gate: the code-immutability guarantee ────────────────────────────────

def _assert_within_home(home: Path, target: Path) -> Path:
    """Refuse any path outside CORVIN_HOME. This is what makes Tier LOCAL unable to
    touch the installed package / repo / site-packages."""
    home_r = home.resolve()
    t = Path(target).resolve()
    if t != home_r and home_r not in t.parents:
        raise RepairScopeError(f"refused write outside CORVIN_HOME: {t}")
    return t


# ── Registry ──────────────────────────────────────────────────────────────────

class RepairAction:
    action_id: str = ""
    risk: str = RISK_SAFE
    blast_radius: str = "home"        # session | tenant | home (documentation)

    def precondition(self, ctx: RepairContext) -> list[Any]:
        """Return the list of fault instances present (empty = nothing to do)."""
        raise NotImplementedError

    def apply(self, ctx: RepairContext, faults: list[Any]) -> int:
        """Repair the faults. Return how many were addressed. Must write only
        within ctx.corvin_home (use _assert_within_home)."""
        raise NotImplementedError

    def undo(self, ctx: RepairContext) -> None:
        """Best-effort reversal of the last apply() (uses recorded before-state)."""
        return None


_REGISTRY: dict[str, RepairAction] = {}


def register_repair(cls: type[RepairAction]) -> type[RepairAction]:
    inst = cls()
    if not inst.action_id:
        raise ValueError(f"{cls.__name__} has no action_id")
    _REGISTRY[inst.action_id] = inst
    return cls


def registered_actions() -> dict[str, RepairAction]:
    return dict(_REGISTRY)


# ── Gating ────────────────────────────────────────────────────────────────────

def _kill_switch() -> bool:
    return os.environ.get("CORVIN_ACO_L5_OFF", "").strip().lower() in ("1", "true", "yes")


def _risky_enabled() -> bool:
    return os.environ.get("CORVIN_ACO_L5_RISKY", "").strip().lower() in ("1", "true", "yes")


# ── Audit (best-effort, never raises) ─────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _audit(ctx: RepairContext, event: str, **fields: Any) -> None:
    rec = {"ts": _now_iso(), "event": event, **fields}
    # 1) durable, append-only repair journal under the home (always local).
    try:
        jp = _assert_within_home(ctx.corvin_home, ctx.corvin_home / "aco_repair.jsonl")
        jp.parent.mkdir(parents=True, exist_ok=True)
        with jp.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — observability, never enforcement
        pass
    # 2) L16 hash-chained audit (metadata only), best-effort.
    try:
        from forge import security_events as _sec  # type: ignore  # noqa: PLC0415
        chain = ctx.corvin_home / "tenants" / ctx.tenant_id / "global" / "audit.jsonl"
        _sec.write_event(chain, event, details={k: v for k, v in fields.items()
                                                 if k in ("action_id", "risk", "status",
                                                          "fixed", "reason")})
    except Exception:  # noqa: BLE001
        pass


# ── Executor ──────────────────────────────────────────────────────────────────

def run_local_repairs(ctx: RepairContext, *, dry_run: bool = False) -> list[RepairOutcome]:
    """Run every applicable repair action once. Loss-gated + reversible + audited.

    Returns one RepairOutcome per action that had work (or would, in dry_run).
    NEVER raises — a failing action is rolled back and recorded, the rest proceed.
    """
    if _kill_switch():
        return []
    risky_ok = _risky_enabled()
    out: list[RepairOutcome] = []

    for action in _REGISTRY.values():
        if action.risk == RISK_RISKY and not risky_ok:
            continue
        try:
            faults = action.precondition(ctx)
        except Exception as exc:  # noqa: BLE001 — detection must not crash the loop
            logger.debug("precondition %s failed: %s", action.action_id, exc)
            continue
        if not faults:
            continue
        if dry_run:
            out.append(RepairOutcome(action.action_id, "would_apply",
                                     f"{len(faults)} fault(s)", fixed=0))
            continue

        _audit(ctx, "repair.attempt", action_id=action.action_id, risk=action.risk,
               count=len(faults))
        try:
            fixed = action.apply(ctx, faults)
            # Loss gate: re-detect. If the fault persists, the action did not help.
            remaining = action.precondition(ctx)
            if remaining:
                action.undo(ctx)
                _audit(ctx, "repair.reverted", action_id=action.action_id,
                       reason="no_improvement", status="reverted")
                out.append(RepairOutcome(action.action_id, "reverted",
                                         "fault persisted → rolled back"))
            else:
                _audit(ctx, "repair.applied", action_id=action.action_id,
                       status="applied", fixed=fixed)
                out.append(RepairOutcome(action.action_id, "applied",
                                         f"cleared {fixed} fault(s)", fixed=fixed))
        except RepairScopeError as exc:
            _audit(ctx, "repair.blocked", action_id=action.action_id, reason=str(exc),
                   status="failed")
            out.append(RepairOutcome(action.action_id, "failed", f"scope: {exc}"))
        except Exception as exc:  # noqa: BLE001
            try:
                action.undo(ctx)
            except Exception:  # noqa: BLE001
                pass
            _audit(ctx, "repair.failed", action_id=action.action_id, reason=str(exc)[:200],
                   status="failed")
            out.append(RepairOutcome(action.action_id, "failed", str(exc)[:200]))
    return out


def run_default(*, tenant_id: str = "_default", dry_run: bool = False) -> list[RepairOutcome]:
    """Convenience entry point: resolve CORVIN_HOME and run."""
    from forge import paths as _paths  # type: ignore  # noqa: PLC0415
    return run_local_repairs(RepairContext(corvin_home=_paths.corvin_home(),
                                           tenant_id=tenant_id), dry_run=dry_run)


# ── SAFE seed actions (M1) ────────────────────────────────────────────────────

@register_repair
class SessionWorkdirMissing(RepairAction):
    """A session meta JSON exists but its on-disk workdir is gone → recreate it.

    Reads the workdir straight from the persisted meta (no path-rebuild → no drift
    with chat_runtime / fs_safe_component). Directly addresses the 'chat exists but
    its folder vanished' class."""
    action_id = "session_workdir_missing"
    risk = RISK_SAFE
    blast_radius = "session"

    def _meta_files(self, ctx: RepairContext) -> list[Path]:
        root = ctx.corvin_home / "tenants"
        if not root.is_dir():
            return []
        return list(root.glob("*/global/web_chat/sessions/*.json"))

    def precondition(self, ctx: RepairContext) -> list[Any]:
        faults = []
        for mf in self._meta_files(ctx):
            try:
                meta = json.loads(mf.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            wd = meta.get("workdir")
            if not wd:
                continue
            wd_path = Path(wd)
            try:
                _assert_within_home(ctx.corvin_home, wd_path)
            except RepairScopeError:
                continue  # never recreate a workdir that escaped the home
            if not wd_path.is_dir():
                faults.append(wd_path)
        return faults

    def apply(self, ctx: RepairContext, faults: list[Any]) -> int:
        self._created: list[Path] = []
        for wd_path in faults:
            t = _assert_within_home(ctx.corvin_home, wd_path)
            t.mkdir(parents=True, exist_ok=True)
            self._created.append(t)
        return len(self._created)

    def undo(self, ctx: RepairContext) -> None:
        for t in reversed(getattr(self, "_created", [])):
            try:
                if t.is_dir() and not any(t.iterdir()):
                    t.rmdir()  # only remove if still empty (we just made it)
            except OSError:
                pass


@register_repair
class StaleLockSweep(RepairAction):
    """Remove orphaned ``*.lock`` files under the home older than the TTL — a crash
    or kill can leave a lock that wedges the next start."""
    action_id = "stale_lock"
    risk = RISK_SAFE
    blast_radius = "home"
    _TTL_S = 3600  # 1 h — well beyond any legitimate lock hold

    def precondition(self, ctx: RepairContext) -> list[Any]:
        faults = []
        now = ctx.time()
        for lk in ctx.corvin_home.rglob("*.lock"):
            try:
                if not lk.is_file():
                    continue
                if now - lk.stat().st_mtime > self._TTL_S:
                    faults.append(lk)
            except OSError:
                continue
        return faults

    def apply(self, ctx: RepairContext, faults: list[Any]) -> int:
        self._removed: list[tuple[Path, bytes]] = []
        n = 0
        for lk in faults:
            t = _assert_within_home(ctx.corvin_home, lk)
            try:
                data = t.read_bytes()
            except OSError:
                data = b""
            t.unlink()
            self._removed.append((t, data))
            n += 1
        return n

    def undo(self, ctx: RepairContext) -> None:
        for t, data in getattr(self, "_removed", []):
            try:
                if not t.exists():
                    t.write_bytes(data)
            except OSError:
                pass


@register_repair
class OrphanTmpSweep(RepairAction):
    """Remove leftover ``*.tmp`` partial-write files under the home older than the
    TTL (interrupted atomic temp+rename writes)."""
    action_id = "orphan_tmp"
    risk = RISK_SAFE
    blast_radius = "home"
    _TTL_S = 86400  # 24 h

    def precondition(self, ctx: RepairContext) -> list[Any]:
        faults = []
        now = ctx.time()
        for tmp in ctx.corvin_home.rglob("*.tmp"):
            try:
                if tmp.is_file() and now - tmp.stat().st_mtime > self._TTL_S:
                    faults.append(tmp)
            except OSError:
                continue
        return faults

    def apply(self, ctx: RepairContext, faults: list[Any]) -> int:
        n = 0
        for tmp in faults:
            t = _assert_within_home(ctx.corvin_home, tmp)
            try:
                t.unlink()
                n += 1
            except OSError:
                pass
        return n

    # undo: an orphan tmp is by definition disposable; no restore needed.


@register_repair
class SecretFileMode(RepairAction):
    """Tighten known secret files under the home to 0600 if looser (POSIX only;
    a no-op on Windows where POSIX bits are not enforced). Tightening-only, so it
    can never widen access."""
    action_id = "secret_file_mode"
    risk = RISK_SAFE
    blast_radius = "home"
    _NAMES = ("service.env", "license.key", "session.key", "identity_registry.json")

    def _candidates(self, ctx: RepairContext) -> list[Path]:
        if os.name == "nt":
            return []  # POSIX mode bits are not meaningful on Windows
        out = []
        for name in self._NAMES:
            out.extend(ctx.corvin_home.rglob(name))
        return [p for p in out if p.is_file()]

    def precondition(self, ctx: RepairContext) -> list[Any]:
        faults = []
        for p in self._candidates(ctx):
            try:
                mode = p.stat().st_mode & 0o777
            except OSError:
                continue
            if mode & 0o077:  # any group/other bit set
                faults.append(p)
        return faults

    def apply(self, ctx: RepairContext, faults: list[Any]) -> int:
        self._prev: list[tuple[Path, int]] = []
        n = 0
        for p in faults:
            t = _assert_within_home(ctx.corvin_home, p)
            try:
                self._prev.append((t, t.stat().st_mode & 0o777))
                t.chmod(0o600)
                n += 1
            except OSError:
                pass
        return n

    def undo(self, ctx: RepairContext) -> None:
        for t, mode in getattr(self, "_prev", []):
            try:
                t.chmod(mode)
            except OSError:
                pass
