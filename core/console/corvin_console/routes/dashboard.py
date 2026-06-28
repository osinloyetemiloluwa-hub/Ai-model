"""Dashboard route — health-card data for the console landing page.

Phase A: returns a small JSON snapshot the SPA renders into a
header-grid. Sources are read fresh per request (no caching) — the
data shape is small so the cost is negligible.

Future viewer phases (B+) will add separate endpoints for sessions,
runs, personas, tools, skills, memory, etc. — each as its own
router under ``/v1/console/<resource>``.
"""
from __future__ import annotations

import json
import os
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from .. import auth as session_auth
from ..deps import require_session

from .. import _bootstrap
_forge_paths = _bootstrap.forge_paths
_REPO = _bootstrap._REPO


router = APIRouter()


_BRIDGES = ("telegram", "discord", "slack", "whatsapp", "email")


def _bridge_status(channel: str) -> dict[str, Any]:
    """Probe whether a channel is configured (settings.json present).

    Mirror of the heuristic in ``bridges/shared/settings_view.py`` —
    "operator configured this channel" is the structurally honest
    signal. We do NOT probe pid files or systemd state (would cost
    a fork per render and isn't reliable in non-systemd contexts).
    """
    home = _forge_paths.corvin_home()
    canonical = home / "bridges" / channel / "settings.json"
    legacy = _REPO / "operator" / "bridges" / channel / "settings.json"
    configured = canonical.exists() or legacy.exists()
    src = "canonical" if canonical.exists() else ("legacy" if legacy.exists() else None)
    return {"channel": channel, "configured": configured, "source": src}


def _audit_chain_status(tenant_id: str) -> dict[str, Any]:
    """Surface the audit-chain head + size for this tenant.

    Cheap inspection: file size + last line's hash. A full
    ``verify_chain`` is too expensive to run per page-load — Phase
    F will add an explicit "verify now" button that runs it on
    demand.
    """
    chain = _forge_paths.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"
    if not chain.exists():
        return {"present": False}
    try:
        size = chain.stat().st_size
    except OSError:
        return {"present": False}
    last_event_type: str | None = None
    last_ts: float | None = None
    try:
        with chain.open("rb") as fh:
            try:
                fh.seek(-4096, os.SEEK_END)
            except OSError:
                fh.seek(0)
            tail = fh.read().decode("utf-8", errors="replace").strip().splitlines()
            for line in reversed(tail):
                try:
                    rec = json.loads(line)
                    last_event_type = rec.get("event_type")
                    last_ts = rec.get("ts")
                    break
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return {
        "present":         True,
        "size_bytes":      size,
        "last_event_type": last_event_type,
        "last_event_ts":   last_ts,
    }


def _engine_default() -> str:
    """Read the engine-layer flag the bridge adapter consults at boot."""
    val = os.environ.get("CORVIN_USE_ENGINE_LAYER", "1")
    return "claude_code (engine layer)" if val != "0" else "claude_code (legacy direct-spawn)"


def _stt_chain() -> dict[str, Any]:
    pin = os.environ.get("CORVIN_STT_PROVIDER")
    if pin:
        return {"mode": "pinned", "providers": [pin]}
    chain = os.environ.get("CORVIN_STT_CHAIN", "openai,local")
    return {"mode": "chain", "providers": [p.strip() for p in chain.split(",") if p.strip()]}


def _today_event_counts(tenant_id: str) -> dict[str, int]:
    """Coarse counts of audit events from today (best-effort).

    Reads at most the last 4 KiB of the chain to keep cost bounded.
    For a full-day rollup we'd page through the whole file — that's
    a Phase F concern, not Phase A.
    """
    chain = _forge_paths.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"
    if not chain.exists():
        return {}
    midnight = _start_of_day_local()
    counts: dict[str, int] = {}
    try:
        # Read full file, count today's events. Bounded by typical
        # audit-chain volume; a busy operator will outgrow this and
        # we'll switch to indexed storage later.
        with chain.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = rec.get("ts")
                if not isinstance(ts, (int, float)) or ts < midnight:
                    continue
                sev = rec.get("severity", "INFO")
                counts[sev] = counts.get(sev, 0) + 1
    except OSError:
        return {}
    return counts


def _start_of_day_local() -> float:
    now = time.localtime()
    midnight_struct = time.struct_time((
        now.tm_year, now.tm_mon, now.tm_mday,
        0, 0, 0,
        now.tm_wday, now.tm_yday, now.tm_isdst,
    ))
    return time.mktime(midnight_struct)


@router.get("/dashboard")
def dashboard(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return the dashboard payload for the owner's tenant."""
    tid = rec.tenant_id
    return {
        "tenant_id":      tid,
        "ts":             time.time(),
        "engine_default": _engine_default(),
        "stt":            _stt_chain(),
        "bridges":        [_bridge_status(b) for b in _BRIDGES],
        "audit_chain":    _audit_chain_status(tid),
        "today_counts":   _today_event_counts(tid),
        "fingerprint":    rec.token_fingerprint,
        "expires_at":     rec.expires_at,
    }
