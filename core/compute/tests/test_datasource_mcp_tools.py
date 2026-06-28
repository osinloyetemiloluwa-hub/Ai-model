"""Tests for DataSource MCP tool definitions and dispatcher (ADR-0026 Section D).

25 test cases:
- datasource_register returns handle + schema_snapshot
- datasource_list returns connection summaries
- datasource_schema returns PII-redacted snapshot
- datasource_test returns {ok, latency_ms}
- datasource_unregister removes manifest + checkpoint
- datasource_preview hard cap at 20 rows (n_rows=100 → ≤ 20)
- datasource_preview applies PII redaction
- FabricNotEnabled returned when fabric_enabled=False
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from corvin_compute.fabric.datasources.mcp_tools import (
    call_datasource_tool,
    datasource_register_tool_def,
    datasource_list_tool_def,
    datasource_schema_tool_def,
    datasource_test_tool_def,
    datasource_unregister_tool_def,
    datasource_preview_tool_def,
    _PREVIEW_MAX_ROWS,
)
from corvin_compute.fabric.datasources.registry import DataSourceRegistry, ConnectionSummary


def _make_registry():
    reg = MagicMock(spec=DataSourceRegistry)
    reg._home = Path("/tmp/fake_home")
    return reg


def _noop_audit(event, details):
    pass


class TestToolDefinitions(unittest.TestCase):
    def test_register_tool_def_has_name(self):
        td = datasource_register_tool_def()
        self.assertEqual(td["name"], "datasource_register")
        self.assertIn("inputSchema", td)

    def test_list_tool_def(self):
        td = datasource_list_tool_def()
        self.assertEqual(td["name"], "datasource_list")

    def test_schema_tool_def(self):
        td = datasource_schema_tool_def()
        self.assertEqual(td["name"], "datasource_schema")
        self.assertIn("name", td["inputSchema"]["properties"])

    def test_test_tool_def(self):
        td = datasource_test_tool_def()
        self.assertEqual(td["name"], "datasource_test")

    def test_unregister_tool_def(self):
        td = datasource_unregister_tool_def()
        self.assertEqual(td["name"], "datasource_unregister")

    def test_preview_tool_def_max_20(self):
        td = datasource_preview_tool_def()
        self.assertEqual(td["name"], "datasource_preview")
        n_rows_schema = td["inputSchema"]["properties"]["n_rows"]
        self.assertEqual(n_rows_schema.get("maximum"), 20)

    def test_preview_max_rows_constant(self):
        self.assertEqual(_PREVIEW_MAX_ROWS, 20)


class TestFabricDisabled(unittest.TestCase):
    def test_fabric_not_enabled_returns_error(self):
        reg = _make_registry()
        result = call_datasource_tool(
            "datasource_list",
            {},
            reg,
            _noop_audit,
            tenant_config={"fabric_enabled": False},
        )
        self.assertIn("error", result)
        self.assertEqual(result["error"], "FabricNotEnabled")

    def test_fabric_enabled_passes_through(self):
        reg = _make_registry()
        reg.list_connections.return_value = []
        result = call_datasource_tool(
            "datasource_list",
            {},
            reg,
            _noop_audit,
            tenant_config={"fabric_enabled": True},
        )
        self.assertNotIn("error", result)


class TestDatasourceRegister(unittest.TestCase):
    def test_register_valid_manifest(self):
        reg = _make_registry()
        manifest_raw = {
            "name": "my-source",
            "adapter": "postgresql",
            "source": {"region": "eu-central-1"},
            "auth": {"method": "vault", "secret_keys": ["PG_USER"]},
            "pii_handling": "redact",
        }
        audit_events = []
        result = call_datasource_tool(
            "datasource_register",
            {"manifest": manifest_raw},
            reg,
            lambda e, d: audit_events.append((e, d)),
        )
        self.assertIn("handle", result)
        self.assertEqual(result["handle"], "my-source")
        self.assertEqual(result["adapter"], "postgresql")
        self.assertEqual(len(audit_events), 1)
        self.assertEqual(audit_events[0][0], "datasource.registered")

    def test_register_invalid_auth_method(self):
        reg = _make_registry()
        result = call_datasource_tool(
            "datasource_register",
            {"manifest": {
                "name": "ds1",
                "adapter": "postgresql",
                "source": {"region": "eu-central-1"},
                "auth": {"method": "basic"},
            }},
            reg,
            _noop_audit,
        )
        self.assertEqual(result["error"], "InvalidAuthMethod")

    def test_register_audit_has_key_names_not_values(self):
        reg = _make_registry()
        audit_events = []
        call_datasource_tool(
            "datasource_register",
            {"manifest": {
                "name": "ds2",
                "adapter": "postgresql",
                "source": {"region": "eu-central-1"},
                "auth": {"method": "vault", "secret_keys": ["PGUSER", "PGPASS"]},
                "pii_handling": "redact",
            }},
            reg,
            lambda e, d: audit_events.append((e, d)),
        )
        _, details = audit_events[0]
        self.assertEqual(details["auth_secret_key_names"], ["PGUSER", "PGPASS"])


class TestDatasourceList(unittest.TestCase):
    def test_list_returns_connection_summaries(self):
        reg = _make_registry()
        reg.list_connections.return_value = [
            ConnectionSummary(name="ds1", adapter="postgresql", region="eu-central-1"),
            ConnectionSummary(name="ds2", adapter="snowflake", region="us-east-1"),
        ]
        result = call_datasource_tool("datasource_list", {}, reg, _noop_audit)
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["connections"][0]["name"], "ds1")

    def test_list_empty_returns_zero(self):
        reg = _make_registry()
        reg.list_connections.return_value = []
        result = call_datasource_tool("datasource_list", {}, reg, _noop_audit)
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["connections"], [])


class TestDatasourceSchema(unittest.TestCase):
    def test_schema_returns_columns(self):
        reg = _make_registry()
        mock_manifest = MagicMock()
        mock_manifest.adapter = "postgresql"
        mock_manifest.schema_hint = {
            "columns": [
                {"name": "id", "dtype": "integer", "pii_tagged": False},
                {"name": "email", "dtype": "string", "pii_tagged": True},
            ]
        }
        reg.load_manifest.return_value = mock_manifest

        result = call_datasource_tool(
            "datasource_schema",
            {"name": "ds1"},
            reg,
            _noop_audit,
        )
        self.assertIn("columns", result)
        self.assertIn("email", result["pii_tagged_columns"])
        self.assertNotIn("id", result["pii_tagged_columns"])

    def test_schema_not_found(self):
        reg = _make_registry()
        reg.load_manifest.side_effect = FileNotFoundError("not found")
        result = call_datasource_tool("datasource_schema", {"name": "x"}, reg, _noop_audit)
        self.assertEqual(result["error"], "NotFound")


class TestDatasourceTest(unittest.TestCase):
    def test_test_returns_ok_and_latency(self):
        reg = _make_registry()
        mock_manifest = MagicMock()
        mock_manifest.adapter = "postgresql"
        reg.load_manifest.return_value = mock_manifest

        result = call_datasource_tool("datasource_test", {"name": "ds1"}, reg, _noop_audit)
        self.assertIn("ok", result)
        self.assertIn("latency_ms", result)
        # The MCP path only validates the manifest — it cannot probe
        # connectivity (no bwrap / no vault), so it MUST NOT report a green
        # ok=True. Real reachability runs via registry.test_connection.
        self.assertFalse(result["ok"])
        self.assertIn("not_tested", result["note"])

    def test_test_emits_audit_event(self):
        reg = _make_registry()
        mock_manifest = MagicMock()
        mock_manifest.adapter = "postgresql"
        reg.load_manifest.return_value = mock_manifest

        events = []
        call_datasource_tool("datasource_test", {"name": "ds1"}, reg,
                             lambda e, d: events.append((e, d)))
        self.assertEqual(events[0][0], "datasource.connection_tested")


class TestDatasourcePreview(unittest.TestCase):
    def test_preview_hard_cap_at_20(self):
        reg = _make_registry()
        mock_manifest = MagicMock()
        mock_manifest.schema_hint = None
        reg.load_manifest.return_value = mock_manifest

        result = call_datasource_tool(
            "datasource_preview",
            {"name": "ds1", "n_rows": 100},  # requesting 100
            reg,
            _noop_audit,
        )
        self.assertLessEqual(result["n_rows_requested"], 20)

    def test_preview_pii_columns_redacted(self):
        reg = _make_registry()
        mock_manifest = MagicMock()
        mock_manifest.schema_hint = {
            "columns": [
                {"name": "id", "dtype": "integer", "pii_tagged": False},
                {"name": "email", "dtype": "string", "pii_tagged": True},
                {"name": "phone", "dtype": "string", "pii_tagged": True},
            ]
        }
        reg.load_manifest.return_value = mock_manifest

        result = call_datasource_tool(
            "datasource_preview",
            {"name": "ds1", "n_rows": 5},
            reg,
            _noop_audit,
        )
        self.assertIn("email", result["pii_columns_redacted"])
        self.assertIn("phone", result["pii_columns_redacted"])
        self.assertNotIn("id", result["pii_columns_redacted"])

    def test_preview_emits_audit_event(self):
        reg = _make_registry()
        mock_manifest = MagicMock()
        mock_manifest.schema_hint = None
        reg.load_manifest.return_value = mock_manifest

        events = []
        call_datasource_tool(
            "datasource_preview", {"name": "ds1"}, reg,
            lambda e, d: events.append((e, d)),
        )
        self.assertEqual(events[0][0], "datasource.preview_generated")

    def test_preview_not_found(self):
        reg = _make_registry()
        reg.load_manifest.side_effect = FileNotFoundError("not found")
        result = call_datasource_tool("datasource_preview", {"name": "x"}, reg, _noop_audit)
        self.assertEqual(result["error"], "NotFound")


class TestDatasourceUnregister(unittest.TestCase):
    def test_unregister_emits_audit(self):
        reg = _make_registry()
        reg._home = Path("/tmp/fake_home_unrg")
        # Make load_manifest raise to simulate non-existing manifest
        reg.load_manifest.side_effect = FileNotFoundError("not found")

        events = []
        result = call_datasource_tool(
            "datasource_unregister",
            {"name": "ds-to-remove"},
            reg,
            lambda e, d: events.append((e, d)),
        )
        self.assertEqual(events[0][0], "datasource.unregistered")
        self.assertEqual(result["removed"], "ds-to-remove")

    def test_unknown_tool_returns_error(self):
        reg = _make_registry()
        result = call_datasource_tool("datasource_unknown_tool", {}, reg, _noop_audit)
        self.assertEqual(result["error"], "UnknownTool")


if __name__ == "__main__":
    unittest.main(verbosity=2)
