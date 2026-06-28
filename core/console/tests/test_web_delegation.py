"""ADR-0114 — web-chat delegation path: triage, flag, budget, spec builder.

Pure-function tests; no subprocess, no network, no ACS spawn.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from corvin_console import chat_runtime as cr  # noqa: E402


# ── triage ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("prompt", [
    "/delegate male ein bild von einem hund",
    "/DELEGATE auch case-insensitiv",
    "Analysiere alle Spieltage der Bundesliga und erstelle danach eine Tabelle "
    "mit den wichtigsten Statistiken pro Verein.",
    "Create a report comparing three frameworks and then summarize the steps.",
    "x" * 400,  # long prompts are substantive by definition
])
def test_triage_delegates_substantive(prompt: str) -> None:
    assert cr._should_delegate(prompt) is True


@pytest.mark.parametrize("prompt", [
    "hallo",
    "wie spät ist es?",
    "danke!",
    "was ist 2+2",
    "erkläre kurz",  # verb-less smalltalk stays direct
])
def test_triage_keeps_trivial_direct(prompt: str) -> None:
    assert cr._should_delegate(prompt) is False


# ── tenant flag + budget ──────────────────────────────────────────────


def _write_tenant_yaml(home: Path, tenant: str, body: str) -> None:
    p = home / "tenants" / tenant / "global"
    p.mkdir(parents=True, exist_ok=True)
    (p / "tenant.corvin.yaml").write_text(body, encoding="utf-8")


def test_delegation_flag_default_deny(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cr._forge_paths, "corvin_home", lambda: tmp_path)
    # No tenant file at all → deny
    assert cr._delegation_enabled("_default") is False
    # File without the key → deny
    _write_tenant_yaml(tmp_path, "_default", "spec:\n  compute:\n    enabled: true\n")
    assert cr._delegation_enabled("_default") is False


def test_delegation_flag_opt_in(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cr._forge_paths, "corvin_home", lambda: tmp_path)
    _write_tenant_yaml(
        tmp_path, "_default",
        "spec:\n  web_chat:\n    delegation_enabled: true\n",
    )
    assert cr._delegation_enabled("_default") is True


def test_delegation_budget_defaults_and_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cr._forge_paths, "corvin_home", lambda: tmp_path)
    assert cr._delegation_budget("_default") == cr._DELEGATION_BUDGET_DEFAULTS
    _write_tenant_yaml(
        tmp_path, "_default",
        "spec:\n  web_chat:\n    budget:\n      max_total_workers: 8\n"
        "      max_wall_time: -5\n      bogus: 99\n",
    )
    b = cr._delegation_budget("_default")
    assert b["max_total_workers"] == 8
    assert b["max_wall_time"] == cr._DELEGATION_BUDGET_DEFAULTS["max_wall_time"]
    assert "bogus" not in b


# ── workflow spec builder ─────────────────────────────────────────────


def test_delegation_spec_is_valid_awp() -> None:
    spec = cr._build_delegation_spec("do the thing", cr._DELEGATION_BUDGET_DEFAULTS)
    assert spec["awp"] == "1.0.0"
    assert spec["workflow"]["description"] == "do the thing"
    assert spec["orchestration"]["engine"] == "delegation_loop"
    assert spec["orchestration"]["delegation_loop"]["budget"] == cr._DELEGATION_BUDGET_DEFAULTS
    # The budget dict must be a copy — callers must not share mutable state.
    spec["orchestration"]["delegation_loop"]["budget"]["max_loops"] = 99
    assert cr._DELEGATION_BUDGET_DEFAULTS["max_loops"] != 99


def test_delegation_spec_passes_acs_validator() -> None:
    shared = Path(__file__).resolve().parents[3] / "operator" / "bridges" / "shared"
    sys.path.insert(0, str(shared))
    try:
        from acs_validator import validate_workflow_dict  # type: ignore
    except ImportError:
        pytest.skip("acs_validator not importable in this environment")
    spec = cr._build_delegation_spec("two-step task", cr._DELEGATION_BUDGET_DEFAULTS)
    result = validate_workflow_dict(spec)
    blocking = [i for i in result.issues if i.severity.upper() in ("ERROR", "CRITICAL")]
    assert not blocking, f"ACS validator rejected the web delegation spec: {blocking}"
