"""Tests for acs_runtime.py — ADR-0104 M2/M3/M4."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

try:
    from . import acs_runtime as _rt
except ImportError:
    import acs_runtime as _rt  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _minimal_spec() -> dict:
    return {
        "awp": "1.0.0",
        "workflow": {
            "name": "test-acs-workflow",
            "description": "A test ACS workflow",
            "version": "1.0.0",
        },
        "orchestration": {
            "engine": "delegation_loop",
            "delegation_loop": {
                "budget": {
                    "max_loops": 5,
                    "max_depth": 2,
                    "max_total_workers": 10,
                    "max_wall_time": 300,
                }
            },
        },
        "state": {"initial": {"topic": "test topic"}},
    }


# ---------------------------------------------------------------------------
# BudgetEnvelope tests
# ---------------------------------------------------------------------------

def test_budget_no_breach():
    b = _rt.BudgetEnvelope(max_loops=10, max_total_tokens=1000)
    b.loops_used = 5
    b.tokens_used = 500
    assert b.check() is None


def test_budget_loops_breach():
    b = _rt.BudgetEnvelope(max_loops=5)
    b.loops_used = 5
    breach = b.check()
    assert breach is not None
    assert "max_loops" in breach


def test_budget_tokens_breach():
    b = _rt.BudgetEnvelope(max_total_tokens=1000)
    b.tokens_used = 1001
    breach = b.check()
    assert breach is not None
    assert "max_total_tokens" in breach


def test_budget_fraction():
    b = _rt.BudgetEnvelope(max_loops=100, max_total_tokens=10000, max_depth=4)
    child = b.fraction(0.5)
    assert child.max_loops == 50
    assert child.max_total_tokens == 5000
    assert child.max_depth == 3  # depth decremented


def test_budget_fraction_depth_floor():
    b = _rt.BudgetEnvelope(max_depth=0)
    child = b.fraction(0.5)
    assert child.max_depth == 0  # floor at 0


# ---------------------------------------------------------------------------
# Manager prompt building
# ---------------------------------------------------------------------------

def test_build_manager_prompt_contains_workflow_id():
    spec = _minimal_spec()
    budget = _rt._budget_from_spec(spec)
    with tempfile.TemporaryDirectory() as td:
        ctx = _rt.RunContext(
            run_id="test-run",
            workflow_id="test-acs-workflow",
            workflow_spec=spec,
            budget=budget,
            run_dir=Path(td),
            state={"topic": "climate risk"},
        )
        prompt = _rt._build_manager_prompt(ctx)
        assert "test-acs-workflow" in prompt
        assert "climate risk" in prompt
        assert "ITERATION" in prompt
        assert "BUDGET REMAINING" in prompt


def test_build_manager_prompt_with_worker_results():
    spec = _minimal_spec()
    budget = _rt._budget_from_spec(spec)
    with tempfile.TemporaryDirectory() as td:
        ctx = _rt.RunContext(
            run_id="test-run",
            workflow_id="test-acs-workflow",
            workflow_spec=spec,
            budget=budget,
            run_dir=Path(td),
            iteration=1,
        )
        ctx.worker_results.append(_rt.WorkerResult(
            worker_id="worker_1",
            status="success",
            result={"analysis": "done"},
            confidence=0.9,
        ))
        prompt = _rt._build_manager_prompt(ctx)
        assert "worker_1" in prompt
        assert "0.90" in prompt


# ---------------------------------------------------------------------------
# Manager decision parsing
# ---------------------------------------------------------------------------

def test_parse_delegate_decision():
    json_text = json.dumps({
        "decision": "DELEGATE",
        "reasoning": "Need to research first",
        "subtasks": [
            {"id": "task_1", "instructions": "research topic", "expected_output": {}}
        ],
    })
    d = _rt._parse_manager_decision(json_text)
    assert d is not None
    assert d["decision"] == "DELEGATE"
    assert len(d["subtasks"]) == 1


def test_parse_complete_decision():
    json_text = json.dumps({
        "decision": "COMPLETE",
        "reasoning": "Task is done",
        "complete_artifacts": {
            "summary": "All done",
            "output_paths": ["report.md"],
            "quality_score": 0.95,
        },
    })
    d = _rt._parse_manager_decision(json_text)
    assert d is not None
    assert d["decision"] == "COMPLETE"


def test_parse_fail_decision():
    json_text = json.dumps({
        "decision": "FAIL",
        "reasoning": "Cannot proceed",
        "fail_reason": "insufficient data",
    })
    d = _rt._parse_manager_decision(json_text)
    assert d is not None
    assert d["decision"] == "FAIL"


def test_parse_decision_from_noisy_output():
    text = (
        "I'll return my decision now.\n\n"
        '{"decision": "DELEGATE", "reasoning": "need work", "subtasks": []}\n\n'
        "That's my answer."
    )
    d = _rt._parse_manager_decision(text)
    assert d is not None
    assert d["decision"] == "DELEGATE"


def test_parse_decision_invalid_json():
    d = _rt._parse_manager_decision("This is not JSON at all.")
    assert d is None


# ---------------------------------------------------------------------------
# Worker output parsing
# ---------------------------------------------------------------------------

def test_parse_worker_success():
    json_text = json.dumps({
        "status": "success",
        "result": {"findings": ["result A", "result B"]},
        "confidence": 0.92,
        "usage": {"llm_tokens": 500, "tool_calls": 3},
    })
    wr = _rt._parse_worker_output(json_text, "worker_1")
    assert wr.status == "success"
    assert wr.confidence == pytest.approx(0.92)
    assert wr.worker_id == "worker_1"
    assert wr.usage["llm_tokens"] == 500


def test_parse_worker_failed():
    json_text = json.dumps({
        "status": "failed",
        "result": {},
        "confidence": 0.0,
        "error": "tool_not_found",
    })
    wr = _rt._parse_worker_output(json_text, "worker_2")
    assert wr.status == "failed"
    assert wr.error == "tool_not_found"


def test_parse_worker_no_json():
    wr = _rt._parse_worker_output("I'm sorry, I couldn't complete this task.", "worker_3")
    assert wr.status == "failed"
    assert "worker_3" == wr.worker_id
    assert wr.error


# ---------------------------------------------------------------------------
# ACSRuntime dry-run
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_acs_dry_run_valid_workflow():
    rt = _rt.ACSRuntime(tenant_id="_test")
    with patch.object(_rt, "_run_dir") as mock_rd, \
         patch.object(_rt, "_write_audit"):
        with tempfile.TemporaryDirectory() as td:
            mock_rd.return_value = Path(td) / "runs" / "test-run"
            result = await rt.run(
                _minimal_spec(),
                dry_run=True,
                run_id="test-run-dry",
            )
    assert result.status == "success"
    assert "dry_run" in result.summary


@pytest.mark.asyncio
async def test_acs_dry_run_invalid_workflow():
    rt = _rt.ACSRuntime(tenant_id="_test")
    bad_spec = {"awp": "invalid", "workflow": {"name": "BadName"}}
    result = await rt.run(bad_spec, dry_run=True)
    assert result.status == "failed"
    assert "validation failed" in result.error


@pytest.mark.asyncio
async def test_acs_file_not_found():
    rt = _rt.ACSRuntime(tenant_id="_test")
    result = await rt.run("/nonexistent/workflow.awp.yaml")
    assert result.status == "failed"
    assert "not found" in result.error


# ---------------------------------------------------------------------------
# Budget from spec
# ---------------------------------------------------------------------------

def test_budget_from_spec_defaults():
    spec = _minimal_spec()
    budget = _rt._budget_from_spec(spec)
    assert budget.max_loops == 5
    assert budget.max_depth == 2
    assert budget.max_total_workers == 10
    assert budget.max_wall_time == 300


def test_budget_from_spec_empty():
    spec = {"orchestration": {}}
    budget = _rt._budget_from_spec(spec)
    assert budget.max_loops == 100
    assert budget.max_depth == 4


# ---------------------------------------------------------------------------
# L34 gate
# ---------------------------------------------------------------------------
# The former `_rt._l34_gate(...)` helper was removed in ADR-0158 M2 — acs_runtime
# now routes L34 through the shared `spawn_gates.check_l34` SSOT (called inline
# with classification=<level>). The behaviour the removed unit tests covered is
# now verified at the SSOT and its integration sites, so re-testing it here would
# only duplicate setup:
#   * DataFlowGuard matrix (PUBLIC/INTERNAL/CONFIDENTIAL/SECRET, unknown-engine
#     fail-closed, secret-egress) — test_data_classification.py
#   * adapter → check_l34 integration with tenant configs (unknown engine id,
#     SECRET blocked on cloud engine, fail-open without config) —
#     test_adapter_compliance_gate.py


# ---------------------------------------------------------------------------
# Worker prompt building
# ---------------------------------------------------------------------------

def test_build_worker_prompt():
    spec = _minimal_spec()
    budget = _rt._budget_from_spec(spec)
    with tempfile.TemporaryDirectory() as td:
        ctx = _rt.RunContext(
            run_id="r1", workflow_id="wf", workflow_spec=spec,
            budget=budget, run_dir=Path(td),
            state={"context": "some context"},
        )
        subtask = {
            "id": "t1",
            "instructions": "Analyse the data.",
            "expected_output": {"type": "object", "properties": {"result": {"type": "string"}}},
            "success_criteria": "Result is non-empty",
        }
        prompt = _rt._build_worker_prompt(subtask, ctx)
        assert "t1" in prompt
        assert "Analyse the data" in prompt
        assert "result" in prompt


def test_build_worker_system_no_delegate():
    spec = _minimal_spec()
    budget = _rt._budget_from_spec(spec)
    with tempfile.TemporaryDirectory() as td:
        ctx = _rt.RunContext(
            run_id="r1", workflow_id="wf", workflow_spec=spec,
            budget=budget, run_dir=Path(td),
        )
        system = _rt._build_worker_system({}, ctx, depth=0, can_delegate=False)
        assert "Worker Agent" in system
        assert "sub-manager" not in system


def test_build_worker_system_with_delegate():
    spec = _minimal_spec()
    budget = _rt._budget_from_spec(spec)
    with tempfile.TemporaryDirectory() as td:
        ctx = _rt.RunContext(
            run_id="r1", workflow_id="wf", workflow_spec=spec,
            budget=budget, run_dir=Path(td),
        )
        ctx.budget.max_depth = 4
        system = _rt._build_worker_system({}, ctx, depth=1, can_delegate=True)
        assert "sub-manager" in system


def test_build_worker_system_with_dynamic_tools():
    spec = _minimal_spec()
    budget = _rt._budget_from_spec(spec)
    with tempfile.TemporaryDirectory() as td:
        ctx = _rt.RunContext(
            run_id="r1", workflow_id="wf", workflow_spec=spec,
            budget=budget, run_dir=Path(td),
        )
        ctx.dynamic_tools["analysis.compute_stats"] = "def compute_stats(): pass"
        system = _rt._build_worker_system({}, ctx, depth=0, can_delegate=False)
        assert "analysis.compute_stats" in system
        assert "DYNAMIC TOOLS AVAILABLE" in system
