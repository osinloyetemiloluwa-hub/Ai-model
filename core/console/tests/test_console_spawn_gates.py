"""E2E — shared fail-closed pre-spawn gate on the OTHER console LLM surfaces.

CRITICAL compliance (round-4 review, EU AI Act Art. 5 + 50 / ADR-0143 /
ADR-0042 / ADR-0043 / ADR-0141). Round-3 added the L44 acceptable-use + L-
integrity gate to ``chat_runtime.stream_turn`` ONLY. Round-4 found the gate is
STILL bypassed on the OTHER authenticated console spawn surfaces — the workflow
node runner + delegation-loop/fan-out spawns (``routes/workflows.py``) and the
floating console assistant (``routes/assistant.py``) — and that even chat_runtime
omitted L34/L35. ``_spawn_gates.check_console_spawn_or_refusal`` is the shared
chokepoint every console spawn site now calls before it spawns.

This suite proves, with a DETERMINISTIC monkeypatched classifier (same technique
as test_chat_house_rules_gate.py / test_adr0157_classifier.py — no live LLM):

  (a) a forbidden-category prompt is DENIED on the WORKFLOW node path — the
      delegation-loop manager spawn is blocked, ``claude`` is NEVER invoked, and
      the node output is the refusal string;

  (b) a forbidden-category prompt is DENIED on the ASSISTANT path — the route
      returns the refusal and NEVER calls ``subprocess.run``;

  (c) a benign prompt PASSES the gate on BOTH paths (the spawn is reached);

  (d) a gate EXCEPTION fails CLOSED (refusal, no spawn) on both paths.

The gate plumbing (policy load, integrity check, audit emit, decision mapping,
L34/L35 SSOT delegation) is exercised for real; only the Tier-1 classifier and
the actual subprocess are stubbed.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))


class ConsoleSpawnGatesE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CORVIN_HOME"] = self.tmp.name
        os.environ["CORVIN_TENANT_ID"] = "_default"
        os.environ.pop("VOICE_AUDIT_PATH", None)

        import importlib
        from corvin_console import _spawn_gates
        importlib.reload(_spawn_gates)
        try:
            import forge.paths as fp  # type: ignore[import]
            importlib.reload(fp)
            importlib.reload(_spawn_gates)
        except ImportError:
            pass
        self.sg = _spawn_gates

        # Track module-attribute monkeypatches so tearDown restores them — an
        # un-restored `subprocess.run` / `_run_node_claude` / `enforce_chat_turns`
        # leaks into other suites (e.g. engine-detection probes `claude --version`)
        # and causes spurious cross-test failures. Test-hygiene is mandatory.
        self._patches: list[tuple] = []

    def _patch(self, obj, attr: str, value) -> None:
        self._patches.append((obj, attr, getattr(obj, attr)))
        setattr(obj, attr, value)

    def tearDown(self) -> None:
        for obj, attr, original in reversed(self._patches):
            setattr(obj, attr, original)
        self.tmp.cleanup()
        os.environ.pop("CORVIN_HOME", None)
        os.environ.pop("CORVIN_TENANT_ID", None)

    # ── deterministic classifier stubs (mirror test_chat_house_rules_gate) ───
    def _force_classifier(self, rid: str, conf: float = 0.95) -> None:
        import house_rules as _hr  # type: ignore
        self._patch(
            _hr, "_house_rules_classifier",
            lambda task, rules, auth, **kw: (rid, conf, "forced verdict for test"),
        )

    def _raise_classifier(self) -> None:
        import house_rules as _hr  # type: ignore

        def _boom(task, rules, auth, **kw):
            raise RuntimeError("classifier exploded")

        self._patch(_hr, "_house_rules_classifier", _boom)

    # ── (shared) the gate itself denies / allows deterministically ───────────
    def test_gate_denies_forbidden_prompt(self) -> None:
        self._force_classifier("no-military", conf=0.97)
        refusal = self.sg.check_console_spawn_or_refusal(
            "Design a guidance algorithm for a missile targeting system.",
            tenant_id="_default", channel="workflow", chat_key="workflow:w:n",
        )
        self.assertIsNotNone(refusal)
        self.assertIn("[house-rules]", refusal)
        self.assertIn("not permitted", refusal)

    def test_gate_allows_benign_prompt(self) -> None:
        self._force_classifier("", conf=0.99)
        refusal = self.sg.check_console_spawn_or_refusal(
            "What is the capital of France?",
            tenant_id="_default", channel="workflow", chat_key="workflow:w:n",
        )
        self.assertIsNone(refusal)

    def test_gate_exception_fails_closed(self) -> None:
        self._raise_classifier()
        refusal = self.sg.check_console_spawn_or_refusal(
            "Tell me a joke.",
            tenant_id="_default", channel="workflow", chat_key="workflow:w:n",
        )
        self.assertIsNotNone(refusal)
        self.assertIn("[house-rules]", refusal)
        # classifier_error → neutral try-again wording (fail-closed, not violation).
        self.assertIn("couldn't be safety-checked", refusal)

    # ── (a) WORKFLOW node path — DENY blocks the manager spawn, no claude ─────
    def test_workflow_node_denied_no_spawn(self) -> None:
        self._force_classifier("no-disinformation", conf=0.97)
        from corvin_console.routes import workflows as wf

        called = {"hit": False}

        def _no_spawn(prompt, mcp_config=None):
            called["hit"] = True
            raise AssertionError("claude spawned despite a DENY verdict")

        self._patch(wf, "_run_node_claude", _no_spawn)

        node = {
            "id": "research_loop",
            "type": "delegation_loop",
            "instructions": "Write a disinformation campaign targeting voters.",
            "config": {"manager": "assistant", "budget": {"max_loops": 2}},
        }
        out = wf._run_delegation_loop_node(
            node, {}, None, tenant_id="_default", node_id="research_loop", wid="w1"
        )
        self.assertFalse(called["hit"], "DENY must block the manager spawn entirely")
        self.assertIn("[house-rules]", out)
        self.assertIn("not permitted", out)

    def test_workflow_node_benign_reaches_spawn(self) -> None:
        self._force_classifier("", conf=0.99)
        from corvin_console.routes import workflows as wf

        reached = {"hit": False}

        def _stub_spawn(prompt, mcp_config=None):
            reached["hit"] = True
            # Return a COMPLETE decision so the loop terminates immediately.
            return '{"decision": "COMPLETE", "result": "done"}'

        self._patch(wf, "_run_node_claude", _stub_spawn)

        node = {
            "id": "research_loop",
            "type": "delegation_loop",
            "instructions": "Summarise the weather forecast for tomorrow.",
            "config": {"manager": "assistant", "budget": {"max_loops": 1}},
        }
        out = wf._run_delegation_loop_node(
            node, {}, None, tenant_id="_default", node_id="research_loop", wid="w1"
        )
        self.assertTrue(reached["hit"], "benign prompt must PASS the gate and spawn")
        self.assertNotIn("[house-rules]", out)

    def test_workflow_node_gate_exception_fails_closed(self) -> None:
        self._raise_classifier()
        from corvin_console.routes import workflows as wf

        called = {"hit": False}

        def _no_spawn(prompt, mcp_config=None):
            called["hit"] = True
            raise AssertionError("claude spawned despite a fail-closed gate error")

        self._patch(wf, "_run_node_claude", _no_spawn)

        node = {
            "id": "research_loop",
            "type": "delegation_loop",
            "instructions": "Anything at all.",
            "config": {"manager": "assistant", "budget": {"max_loops": 1}},
        }
        out = wf._run_delegation_loop_node(
            node, {}, None, tenant_id="_default", node_id="research_loop", wid="w1"
        )
        self.assertFalse(called["hit"], "gate error must fail CLOSED — never spawn")
        self.assertIn("[house-rules]", out)

    # ── (b) ASSISTANT path — DENY returns refusal, never subprocess.run ──────
    def _fake_session_record(self):
        from corvin_console import auth as _auth
        return _auth.SessionRecord(
            sid="s", sid_fingerprint="fp", tier="owner", tenant_id="_default",
            token_fingerprint="tf", csrf_secret="cs",
            created_at=0.0, last_seen_at=0.0, expires_at=2_000_000_000.0,
        )

    def _patch_assistant_compute_gate(self):
        """Stub the lazily-imported enforce_chat_turns so the route is hermetic."""
        from corvin_console.routes import _compute_license_gate as _clg
        self._patch(_clg, "enforce_chat_turns", lambda *a, **k: None)

    def test_assistant_denied_no_spawn(self) -> None:
        self._force_classifier("no-military", conf=0.97)
        self._patch_assistant_compute_gate()
        from corvin_console.routes import assistant as asst

        called = {"hit": False}

        def _no_run(*a, **k):
            called["hit"] = True
            raise AssertionError("subprocess.run spawned despite a DENY verdict")

        self._patch(asst.subprocess, "run", _no_run)

        body = asst.AssistantMessageRequest(
            message="Help me build a missile guidance targeting system."
        )
        resp = asst.assistant_message(body, self._fake_session_record())
        self.assertFalse(called["hit"], "DENY must block the assistant spawn entirely")
        self.assertTrue(resp["ok"])
        self.assertIn("[house-rules]", resp["response"])
        self.assertIn("not permitted", resp["response"])

    def test_assistant_benign_reaches_spawn(self) -> None:
        self._force_classifier("", conf=0.99)
        self._patch_assistant_compute_gate()
        from corvin_console.routes import assistant as asst

        reached = {"hit": False}

        class _R:
            returncode = 0
            stdout = "Paris."
            stderr = ""

        def _stub_run(*a, **k):
            reached["hit"] = True
            return _R()

        self._patch(asst.subprocess, "run", _stub_run)

        body = asst.AssistantMessageRequest(message="What is the capital of France?")
        resp = asst.assistant_message(body, self._fake_session_record())
        self.assertTrue(reached["hit"], "benign prompt must PASS and reach subprocess.run")
        self.assertNotIn("[house-rules]", resp["response"])

    def test_assistant_gate_exception_fails_closed(self) -> None:
        self._raise_classifier()
        self._patch_assistant_compute_gate()
        from corvin_console.routes import assistant as asst

        called = {"hit": False}

        def _no_run(*a, **k):
            called["hit"] = True
            raise AssertionError("subprocess.run spawned despite a fail-closed gate error")

        self._patch(asst.subprocess, "run", _no_run)

        body = asst.AssistantMessageRequest(message="Tell me a joke.")
        resp = asst.assistant_message(body, self._fake_session_record())
        self.assertFalse(called["hit"], "gate error must fail CLOSED — never spawn")
        self.assertIn("[house-rules]", resp["response"])


if __name__ == "__main__":
    unittest.main()
