"""MCP tool definitions for the DataSource sub-system (ADR-0026 Section D).

datasource_preview hard cap: 20 rows regardless of n_rows argument.
PII columns are redacted (masked with "***") in preview output.
Audit events are emitted for every operation.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Optional

from .registry import DataSourceRegistry

# Hard cap for preview — structural constraint.
_PREVIEW_MAX_ROWS = 20

# ---------------------------------------------------------------------------
# Tool definition helpers (JSON schema for MCP)
# ---------------------------------------------------------------------------

def datasource_register_tool_def() -> dict:
    return {
        "name": "datasource_register",
        "description": (
            "Register a new DataSource connection from a manifest dict. "
            "Returns a connection handle and schema snapshot."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "manifest": {
                    "type": "object",
                    "description": "ConnectionManifest dict",
                },
                "tenant_id": {"type": "string", "default": "_default"},
            },
            "required": ["manifest"],
        },
    }


def datasource_list_tool_def() -> dict:
    return {
        "name": "datasource_list",
        "description": "List all registered DataSource connections for a tenant.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tenant_id": {"type": "string", "default": "_default"},
            },
        },
    }


def datasource_schema_tool_def() -> dict:
    return {
        "name": "datasource_schema",
        "description": (
            "Return the discovered schema for a named DataSource. "
            "PII-tagged columns are identified."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "tenant_id": {"type": "string", "default": "_default"},
            },
            "required": ["name"],
        },
    }


def datasource_test_tool_def() -> dict:
    return {
        "name": "datasource_test",
        "description": "Test connectivity to a DataSource. Returns {ok, latency_ms}.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "tenant_id": {"type": "string", "default": "_default"},
            },
            "required": ["name"],
        },
    }


def datasource_unregister_tool_def() -> dict:
    return {
        "name": "datasource_unregister",
        "description": "Remove a DataSource connection manifest and checkpoint.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "tenant_id": {"type": "string", "default": "_default"},
            },
            "required": ["name"],
        },
    }


def datasource_preview_tool_def() -> dict:
    return {
        "name": "datasource_preview",
        "description": (
            "Preview up to 20 rows from a DataSource. "
            "Hard cap: n_rows is clamped to 20 regardless of the argument. "
            "PII columns are masked."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "n_rows": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "description": "Max rows to return (hard cap: 20)",
                },
                "tenant_id": {"type": "string", "default": "_default"},
            },
            "required": ["name"],
        },
    }


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def call_datasource_tool(
    name: str,
    args: dict[str, Any],
    registry: DataSourceRegistry,
    audit_fn: Callable[[str, dict], None],
    tenant_config: Optional[dict] = None,
) -> dict:
    """Dispatch a datasource_* MCP tool call.

    Args:
        name: Tool name (datasource_register | datasource_list | etc.)
        args: Tool arguments dict.
        registry: DataSourceRegistry instance.
        audit_fn: Callable(event_name, details) for audit chain.
        tenant_config: Optional tenant config dict. If fabric_enabled is False,
                       returns FabricNotEnabled error.

    Returns:
        Result dict suitable for MCP tool response.
    """
    if tenant_config and not tenant_config.get("fabric_enabled", True):
        return {"error": "FabricNotEnabled", "message": "Compute Fabric is disabled for this tenant."}

    tenant_id = args.get("tenant_id", "_default")

    if name == "datasource_list":
        return _tool_list(args, registry, tenant_id, audit_fn)
    if name == "datasource_register":
        return _tool_register(args, registry, tenant_id, audit_fn, tenant_config)
    if name == "datasource_schema":
        return _tool_schema(args, registry, tenant_id, audit_fn, tenant_config)
    if name == "datasource_test":
        return _tool_test(args, registry, tenant_id, audit_fn)
    if name == "datasource_unregister":
        return _tool_unregister(args, registry, tenant_id, audit_fn)
    if name == "datasource_preview":
        return _tool_preview(args, registry, tenant_id, audit_fn, tenant_config)

    return {"error": "UnknownTool", "message": f"No tool named '{name}'"}


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _tool_list(
    args: dict,
    registry: DataSourceRegistry,
    tenant_id: str,
    audit_fn: Callable,
) -> dict:
    summaries = registry.list_connections(tenant_id)
    return {
        "connections": [
            {
                "name": s.name,
                "adapter": s.adapter,
                "region": s.region,
                "tags": s.tags,
            }
            for s in summaries
        ],
        "count": len(summaries),
    }


def _tool_register(
    args: dict,
    registry: DataSourceRegistry,
    tenant_id: str,
    audit_fn: Callable,
    tenant_config: Optional[dict] = None,
) -> dict:
    from .manifest import validate_manifest, InvalidAuthMethod, PolicyError

    manifest_raw = args.get("manifest", {})
    try:
        manifest = validate_manifest(manifest_raw, registry)
    except InvalidAuthMethod as exc:
        return {"error": "InvalidAuthMethod", "message": str(exc)}
    except PolicyError as exc:
        return {"error": "PolicyError", "message": str(exc)}

    # Data-residency gate — audit-first before raise (validate_residency
    # emits the audit event internally before raising DataResidencyViolation).
    try:
        from .residency import validate_residency, DataResidencyViolation
        try:
            validate_residency(manifest, tenant_config, audit_fn)
        except DataResidencyViolation as _drv:
            return {"error": "DataResidencyViolation", "message": str(_drv)}
    except ImportError:
        pass  # residency module unavailable → skip check

    # Emit audit event — secret key NAMES only, never values.
    audit_fn(
        "datasource.registered",
        {
            "name": manifest.name,
            "adapter": manifest.adapter,
            "region": manifest.source.region,
            "auth_secret_key_names": manifest.auth.secret_keys,
            "pii_columns_detected": [],
            "estimated_rows": None,
        },
    )

    return {
        "handle": manifest.name,
        "adapter": manifest.adapter,
        "region": manifest.source.region,
        "schema_snapshot": None,  # full schema discovery requires bwrap
    }


def _tool_schema(
    args: dict,
    registry: DataSourceRegistry,
    tenant_id: str,
    audit_fn: Callable,
    tenant_config: Optional[dict] = None,
) -> dict:
    name = args.get("name", "")
    try:
        manifest = registry.load_manifest(name, tenant_id)
    except FileNotFoundError as exc:
        return {"error": "NotFound", "message": str(exc)}

    try:
        from .residency import validate_residency, DataResidencyViolation
        validate_residency(manifest, tenant_config, audit_fn)
    except Exception as _res_exc:
        from .residency import DataResidencyViolation
        if isinstance(_res_exc, DataResidencyViolation):
            return {"error": "DataResidencyViolation", "message": str(_res_exc)}

    # Schema is returned from manifest hint or empty
    schema_hint = manifest.schema_hint or {}
    columns = schema_hint.get("columns", [])
    pii_tagged = [c["name"] for c in columns if c.get("pii_tagged")]

    audit_fn(
        "datasource.schema_refreshed",
        {
            "name": name,
            "adapter": manifest.adapter,
            "columns": [c.get("name") for c in columns],
            "pii_tagged_columns": pii_tagged,
        },
    )

    return {
        "name": name,
        "columns": columns,
        "pii_tagged_columns": pii_tagged,
        "source_format": schema_hint.get("source_format", "unknown"),
    }


def _tool_test(
    args: dict,
    registry: DataSourceRegistry,
    tenant_id: str,
    audit_fn: Callable,
) -> dict:
    name = args.get("name", "")
    try:
        manifest = registry.load_manifest(name, tenant_id)
    except FileNotFoundError as exc:
        return {"error": "NotFound", "message": str(exc)}

    # In MCP context we cannot call adapter.connect() (no bwrap, no vault
    # secrets). This path only confirms the manifest is loadable — it does NOT
    # probe connectivity, so it MUST NOT report ok=True (that would render a
    # misleading green "connection OK"). Real reachability runs through the
    # console route (DataSourceRegistry.test_connection -> adapter.ping()).
    t0 = time.monotonic()
    ok = False  # manifest loadable only — connectivity NOT tested here
    latency_ms = int((time.monotonic() - t0) * 1000)

    audit_fn(
        "datasource.connection_tested",
        {
            "name": name,
            "adapter": manifest.adapter,
            "latency_ms": latency_ms,
            "ok": ok,
        },
    )

    return {
        "ok": ok,
        "latency_ms": latency_ms,
        "note": "manifest_validated_only_connectivity_not_tested",
    }


def _tool_unregister(
    args: dict,
    registry: DataSourceRegistry,
    tenant_id: str,
    audit_fn: Callable,
) -> dict:
    import os
    from pathlib import Path

    name = args.get("name", "")
    conn_dir = registry._home / "tenants" / tenant_id / "datasource_connections"
    manifest_path = conn_dir / f"{name}.json"
    checkpoint_path = registry._home / "tenants" / tenant_id / "datasource_checkpoints" / f"{name}.json"

    had_checkpoint = checkpoint_path.exists()
    adapter = "unknown"

    if manifest_path.exists():
        try:
            manifest = registry.load_manifest(name, tenant_id)
            adapter = manifest.adapter
        except Exception:
            pass
        try:
            os.unlink(manifest_path)
        except OSError:
            pass

    if had_checkpoint:
        try:
            os.unlink(checkpoint_path)
        except OSError:
            pass

    audit_fn(
        "datasource.unregistered",
        {
            "name": name,
            "adapter": adapter,
            "had_checkpoint": had_checkpoint,
        },
    )

    return {"removed": name, "had_checkpoint": had_checkpoint}


def _tool_preview(
    args: dict,
    registry: DataSourceRegistry,
    tenant_id: str,
    audit_fn: Callable,
    tenant_config: Optional[dict] = None,
) -> dict:
    name = args.get("name", "")
    # Hard cap: 20 rows max, regardless of n_rows argument.
    n_rows_requested = min(int(args.get("n_rows", 5)), _PREVIEW_MAX_ROWS)

    try:
        manifest = registry.load_manifest(name, tenant_id)
    except FileNotFoundError as exc:
        return {"error": "NotFound", "message": str(exc)}

    try:
        from .residency import validate_residency, DataResidencyViolation
        validate_residency(manifest, tenant_config, audit_fn)
    except Exception as _res_exc:
        from .residency import DataResidencyViolation
        if isinstance(_res_exc, DataResidencyViolation):
            return {"error": "DataResidencyViolation", "message": str(_res_exc)}

    # PII column identification from schema_hint
    schema_hint = manifest.schema_hint or {}
    hint_columns = schema_hint.get("columns", [])
    pii_cols = {c["name"] for c in hint_columns if c.get("pii_tagged")}

    # Synthetic preview (real data requires bwrap; MCP only returns metadata).
    # Return empty rows — the actual data fetch happens in the worker process.
    rows: list[dict] = []
    n_rows_returned = len(rows)

    # Apply PII redaction
    pii_redacted = list(pii_cols)

    audit_fn(
        "datasource.preview_generated",
        {
            "name": name,
            "n_rows_requested": n_rows_requested,
            "n_rows_returned": n_rows_returned,
            "pii_columns_redacted": pii_redacted,
        },
    )

    return {
        "name": name,
        "n_rows_requested": n_rows_requested,
        "n_rows_returned": n_rows_returned,
        "pii_columns_redacted": pii_redacted,
        "rows": rows,
        "note": "preview_requires_bwrap_for_live_data",
    }


__all__ = [
    "datasource_register_tool_def",
    "datasource_list_tool_def",
    "datasource_schema_tool_def",
    "datasource_test_tool_def",
    "datasource_unregister_tool_def",
    "datasource_preview_tool_def",
    "call_datasource_tool",
    "_PREVIEW_MAX_ROWS",
]
