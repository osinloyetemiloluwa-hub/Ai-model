"""DSI v2 HTTP Adapter Registry (ADR-0124 M4).

Any HTTP server implementing /ping, /schema, /query can be registered
as a data source adapter. The bridge protocol is simple and language-agnostic.

Routes:
  GET    /data-sources/adapters/http                  list all HTTP adapters
  PUT    /data-sources/adapters/http/{adapter_id}     register or update
  DELETE /data-sources/adapters/http/{adapter_id}     remove
  POST   /data-sources/adapters/http/{adapter_id}/ping  connectivity test
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel, Field

from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_csrf, require_session

from .. import _bootstrap
_forge_paths = _bootstrap.forge_paths

# ADR-0147 CON-DS-V2-01: license gate parity with the DSI-v1 register path.
# _bootstrap (imported above) already put operator/ and operator/license/ on
# sys.path, so the license import resolves without per-file path math.
# Fail-closed FREE_TIER fallback for the limits this route reads. A bare
# ``{}.get`` would return None for every feature, and None is the "unlimited"
# sentinel — so an unimportable license package would FAIL OPEN (every HTTP
# adapter allowed). Hard-code the FREE_TIER cap inline so the gate stays
# fail-closed: free tier allows only local-file connections (no "http"/"dsi_v2_http").
_DS_FREE_TIER_FALLBACK: dict = {"datasource_adapters_allowed": ["local_file"]}

try:
    from license.validator import get_limit as _lic_get_limit  # type: ignore[import]
except ImportError:
    try:
        from license.limits import FREE_TIER as _FREE_TIER  # type: ignore[import]
        _lic_get_limit = _FREE_TIER.get  # type: ignore[assignment]
    except ImportError:
        # Innermost fallback: license package entirely absent. Resolve via the
        # hard-coded FREE_TIER caps (fail-closed), never to None=unlimited.
        _lic_get_limit = _DS_FREE_TIER_FALLBACK.get  # type: ignore[assignment]

import logging
_log = logging.getLogger(__name__)

router = APIRouter()

_VALID_AUTH = frozenset({"none", "bearer", "api_key"})
_VALID_LOCALITIES = frozenset({"local", "eu_cloud", "us_cloud"})
_VALID_EGRESS = frozenset({"none", "restricted", "full"})
_ADAPTER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


# ── Storage ───────────────────────────────────────────────────────────────────

def _adapters_dir(tid: str) -> Path:
    return _forge_paths.tenant_global_dir(tid) / "datasources" / "http"


def _adapter_path(tid: str, adapter_id: str) -> Path:
    return _adapters_dir(tid) / f"{adapter_id}.json"


def _load_adapter(tid: str, adapter_id: str) -> dict[str, Any] | None:
    p = _adapter_path(tid, adapter_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _list_adapters(tid: str) -> list[dict[str, Any]]:
    d = _adapters_dir(tid)
    if not d.is_dir():
        return []
    results = []
    for p in sorted(d.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                masked = {k: v for k, v in data.items() if k != "_base_url"}
                results.append(masked)
        except (OSError, json.JSONDecodeError):
            pass
    return results


def _write_adapter(tid: str, adapter_id: str, data: dict[str, Any]) -> None:
    p = _adapter_path(tid, adapter_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2, ensure_ascii=False))
        os.replace(tmp, str(p))
        os.chmod(str(p), 0o600)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _hash_url(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


# ── Models ────────────────────────────────────────────────────────────────────

class HttpAdapterRequest(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=100)
    base_url: str = Field(..., min_length=1, max_length=500)
    auth_type: str = Field("none", description="none | bearer | api_key")
    auth_env: str | None = Field(None, description="Vault env-var name for credentials")
    auth_header: str | None = Field(None, description="Custom header name (for api_key)")
    locality: str = Field("local")
    network_egress: str = Field("none")
    description: str = Field("", max_length=500)
    model_config = {"extra": "forbid"}


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/data-sources/adapters/http")
def list_http_adapters(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    adapters = _list_adapters(rec.tenant_id)
    return {"tenant_id": rec.tenant_id, "count": len(adapters), "adapters": adapters}


@router.put("/data-sources/adapters/http/{adapter_id}")
def register_http_adapter(
    adapter_id: str,
    body: HttpAdapterRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    if not _ADAPTER_ID_RE.match(adapter_id):
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            "adapter_id must be lowercase alphanumeric with _ or -",
        )
    if body.auth_type not in _VALID_AUTH:
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            f"auth_type must be one of {sorted(_VALID_AUTH)}",
        )
    if body.locality not in _VALID_LOCALITIES:
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            f"locality must be one of {sorted(_VALID_LOCALITIES)}",
        )
    if body.network_egress not in _VALID_EGRESS:
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            f"network_egress must be one of {sorted(_VALID_EGRESS)}",
        )

    # ADR-0147 CON-DS-V2-01: gate DSI-v2 HTTP registration with the same
    # datasource_adapters_allowed allowlist enforced on the DSI-v1 path
    # (data_sources.py). Every HTTP adapter is a remote (non-local_file) source;
    # FREE_TIER allows only ["local_file"]. Fail-closed: a missing license module
    # resolves via FREE_TIER, never to "all adapters". Skip the gate for an UPDATE
    # of an already-registered adapter (it was admitted under whatever tier applied
    # at create time; we only gate net-new remote adapters).
    _dl_allowed = _lic_get_limit("datasource_adapters_allowed")
    if (
        _load_adapter(rec.tenant_id, adapter_id) is None  # net-new only
        and _dl_allowed is not None
        and "http" not in _dl_allowed
        and "dsi_v2_http" not in _dl_allowed
    ):
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="datasource.http_adapter_registered",
            target_kind="http_adapter",
            target_id=adapter_id,
            reason="license_limit_exceeded",
        )
        raise HTTPException(
            status_code=402,
            detail={
                "error": "license_limit",
                "feature": "datasource_adapters_allowed",
                "msg": "Remote HTTP data-source adapters require a paid tier "
                       "(free tier is local-file only).",
                "upgrade_url": "https://corvin-labs.com/pricing",
            },
        )

    existing = _load_adapter(rec.tenant_id, adapter_id)
    is_update = existing is not None

    manifest: dict[str, Any] = {
        "adapter_id": adapter_id,
        "display_name": body.display_name,
        "base_url_hash": _hash_url(body.base_url),
        "_base_url": body.base_url,
        "auth_type": body.auth_type,
        "auth_env": body.auth_env,
        "auth_header": body.auth_header,
        "locality": body.locality,
        "network_egress": body.network_egress,
        "description": body.description,
        "protocol": "dsi_v2_http",
        "created_at": existing.get("created_at", time.time()) if existing else time.time(),
        "updated_at": time.time(),
    }

    try:
        _write_adapter(rec.tenant_id, adapter_id, manifest)
    except OSError as exc:
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, "storage error") from exc

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="datasource.http_adapter_updated" if is_update else "datasource.http_adapter_registered",
        target_kind="http_adapter",
        target_id=adapter_id,
    )
    return {"ok": True, "adapter_id": adapter_id, "updated": is_update}


@router.delete("/data-sources/adapters/http/{adapter_id}")
def remove_http_adapter(
    adapter_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    p = _adapter_path(rec.tenant_id, adapter_id)
    if not p.exists():
        raise HTTPException(
            http_status.HTTP_404_NOT_FOUND,
            f"adapter {adapter_id!r} not found",
        )
    try:
        p.unlink()
    except OSError as exc:
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, "delete failed") from exc

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="datasource.http_adapter_removed",
        target_kind="http_adapter",
        target_id=adapter_id,
    )
    return {"ok": True, "adapter_id": adapter_id}


@router.post("/data-sources/adapters/http/{adapter_id}/ping")
def ping_http_adapter(
    adapter_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    manifest = _load_adapter(rec.tenant_id, adapter_id)
    if manifest is None:
        raise HTTPException(
            http_status.HTTP_404_NOT_FOUND,
            f"adapter {adapter_id!r} not found",
        )

    base_url = manifest.get("_base_url", "")
    ping_url = base_url.rstrip("/") + "/ping"

    try:
        req = urllib.request.Request(ping_url, method="GET")
        auth_type = manifest.get("auth_type", "none")
        auth_env = manifest.get("auth_env")
        if auth_type == "bearer" and auth_env:
            token = os.environ.get(auth_env, "")
            if token:
                req.add_header("Authorization", f"Bearer {token}")
        elif auth_type == "api_key" and auth_env:
            header = manifest.get("auth_header", "X-API-Key")
            token = os.environ.get(auth_env, "")
            if token:
                req.add_header(header, token)

        with urllib.request.urlopen(req, timeout=5.0) as resp:
            body = json.loads(resp.read())

        console_audit.action_performed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="datasource.http_adapter_pinged",
            target_kind="http_adapter",
            target_id=adapter_id,
        )
        return {
            "ok": True,
            "adapter_id": adapter_id,
            "reachable": True,
            "name": body.get("name", ""),
            "version": body.get("version", ""),
        }
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "adapter_id": adapter_id,
            "reachable": False,
            "http_status": exc.code,
            "error": "unreachable",
        }
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "adapter_id": adapter_id, "reachable": False, "error": "internal error"}
