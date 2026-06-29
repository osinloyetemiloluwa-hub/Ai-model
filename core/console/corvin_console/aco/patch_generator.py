"""ACO Layer 6 — engine-backed patch generator (ADR-0178).

The fully-automatic ``patch_source`` for ``run_maintenance_loop``: takes an ACO L4
diagnosis, hands the engine (LLM) the diagnosis + the current target-file contents,
and parses the engine's reply into a :class:`Patch`.

SECURITY — engine-generated code is NEVER auto-merged to ``main``. We force the
patch's ``risk_class`` to ``"engine_generated"`` (deliberately NOT in the L6
low-risk allowlist), so ``l6_gate`` always sets ``requires_ack`` and the loop can
only ever produce a PR for human review — regardless of what risk_class the LLM
claims. An LLM cannot talk its way to a direct-to-main merge. The l6_gate
(tests-green, hard-block, path-scope) remains the backstop on top of that.

The ``llm`` is an injected ``str -> str`` callable (testable with a stub).
``default_llm`` builds a WorkerEngine-backed one, or returns None when no engine
is available (→ the loop escalates; it never invents code).
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Optional

from .maintenance_loop import Patch, PatchEdit

logger = logging.getLogger(__name__)

# Engine-generated patches are ALWAYS ack-gated (never auto-merged). This token
# is intentionally absent from maintenance_loop._LOW_RISK_CLASSES.
ENGINE_RISK_CLASS = "engine_generated"

_MAX_EDITS = 8
_MAX_FILE_BYTES = 200_000


def _target_files(diagnosis: dict) -> list[str]:
    files = diagnosis.get("files")
    if isinstance(files, list) and files:
        return [str(f) for f in files]
    one = diagnosis.get("file")
    return [str(one)] if one else []


def build_prompt(diagnosis: dict, file_contents: dict[str, str]) -> str:
    """Construct the patch-generation prompt (metadata + current file bodies)."""
    diag_json = json.dumps({k: diagnosis.get(k) for k in
                            ("id", "root_cause", "layer", "anomaly_class", "repro",
                             "file", "files", "summary") if k in diagnosis},
                           indent=2, ensure_ascii=False)
    parts = [
        "You are a CorvinOS maintenance engine. Fix the diagnosed bug.",
        "Return ONLY a JSON object, no prose, of the exact shape:",
        '{"summary": "<one line>", "edits": '
        '[{"path": "<repo-relative>", "new_content": "<FULL new file content>"}]}',
        "Rules: edit only the file(s) named in the diagnosis; return the COMPLETE "
        "new content of each edited file (not a diff); make the smallest change "
        "that fixes the root cause; never touch LICENSE/NOTICE/CLA/audit/policy/keys.",
        "",
        "DIAGNOSIS:",
        diag_json,
        "",
        "CURRENT FILE(S):",
    ]
    for path, body in file_contents.items():
        parts.append(f"--- {path} ---")
        parts.append(body)
    return "\n".join(parts)


def parse_patch(text: str, diagnosis: dict, *, max_edits: int = _MAX_EDITS) -> Optional[Patch]:
    """Robustly extract the JSON patch from the engine reply. Returns None on any
    malformation (the loop then escalates — never invents code)."""
    if not text:
        return None
    # strip ```json fences / leading prose: take the outermost {...}.
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    raw_edits = obj.get("edits")
    if not isinstance(raw_edits, list) or not raw_edits:
        return None
    if len(raw_edits) > max_edits:
        return None
    edits: list[PatchEdit] = []
    for e in raw_edits:
        if not isinstance(e, dict):
            return None
        path = e.get("path")
        content = e.get("new_content")
        if not isinstance(path, str) or not isinstance(content, str):
            return None
        # repo-relative only — no absolute, no traversal (the loop also enforces
        # this, but the generator must not even propose an escape).
        norm = path.replace("\\", "/")
        if norm.startswith("/") or ".." in norm.split("/") or ":" in norm.split("/")[0][1:2]:
            return None
        if len(content.encode("utf-8")) > _MAX_FILE_BYTES:
            return None
        edits.append(PatchEdit(path=norm, new_content=content))
    return Patch(
        diagnosis_id=str(diagnosis.get("id") or "diag"),
        summary=str(obj.get("summary") or "engine maintenance fix")[:200],
        risk_class=ENGINE_RISK_CLASS,   # FORCED — never the LLM's self-claim.
        edits=edits,
    )


def engine_patch_source(*, repo_dir: str | Path,
                        llm: Optional[Callable[[str], str]] = None,
                        max_edits: int = _MAX_EDITS) -> Callable[[dict], Optional[Patch]]:
    """Build a patch_source for run_maintenance_loop. Reads the diagnosis's target
    file(s) from repo_dir, prompts ``llm``, parses the reply. llm=None → always
    None (escalate)."""
    repo = Path(repo_dir)

    def src(diagnosis: dict) -> Optional[Patch]:
        if llm is None:
            return None
        targets = _target_files(diagnosis)
        if not targets:
            return None
        bodies: dict[str, str] = {}
        for rel in targets:
            norm = rel.replace("\\", "/")
            if norm.startswith("/") or ".." in norm.split("/"):
                return None
            fp = (repo / norm)
            try:
                if fp.is_file() and fp.stat().st_size <= _MAX_FILE_BYTES:
                    bodies[norm] = fp.read_text(encoding="utf-8")
                else:
                    bodies[norm] = ""   # new file
            except OSError:
                bodies[norm] = ""
        try:
            reply = llm(build_prompt(diagnosis, bodies))
        except Exception as exc:  # noqa: BLE001 — engine failure → escalate
            logger.debug("engine patch llm failed: %s", exc)
            return None
        return parse_patch(reply, diagnosis, max_edits=max_edits)

    return src


def default_llm(*, model: Optional[str] = None, timeout: float = 300.0,
                working_dir: Optional[str | Path] = None) -> Optional[Callable[[str], str]]:
    """Build a WorkerEngine-backed ``str -> str`` completion, or None if no engine
    is importable/available (→ escalate). Best-effort; never raises at build time."""
    try:
        import sys as _sys
        from pathlib import Path as _P
        _shared = _P(__file__).resolve().parents[3] / "operator" / "bridges" / "shared"
        if str(_shared) not in _sys.path:
            _sys.path.insert(0, str(_shared))
        from agents.claude_code import ClaudeCodeEngine  # type: ignore
        from agents import collect  # type: ignore
    except Exception:  # noqa: BLE001
        return None

    def _llm(prompt: str) -> str:
        engine = ClaudeCodeEngine()
        kw: dict[str, Any] = {"timeout": timeout}
        if model:
            kw["model"] = model
        if working_dir:
            kw["working_dir"] = _P(working_dir)
        result = collect(engine.spawn(prompt, **kw))
        return result.final_text or ""

    return _llm
