"""ADR-0149 LIC-ENG-USE-01: run_delegate must enforce the license engines_allowed
limit fail-closed — a SesT restricting engines must block a forbidden worker
engine on the delegation USE path (the adapter OS-turn path already did)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parents[1]  # core/delegate
_REPO = _PLUGIN_DIR.parents[1]
for _p in (
    str(_PLUGIN_DIR),
    str(_REPO / "operator" / "bridges" / "shared"),  # agents package parent
    str(_REPO / "operator"),                          # license package
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from corvin_delegate.delegation import run_delegate  # noqa: E402
import license.validator as _v  # noqa: E402


class TestEnginesAllowedGate(unittest.TestCase):
    def setUp(self):
        # Ensure the dual-env test bypass is NOT both-set, so the gate fires.
        import os
        for k in ("CORVIN_AGENTS_SKIP_LIVE", "CORVIN_INTEGRATION_TEST"):
            os.environ.pop(k, None)
        self._orig = _v._ACTIVE_LICENSE
        self._orig_canary = _v._ACTIVE_LICENSE_CANARY

    def tearDown(self):
        _v._ACTIVE_LICENSE = self._orig
        _v._ACTIVE_LICENSE_CANARY = self._orig_canary

    def test_forbidden_engine_denied_by_license(self):
        # SesT restricts engines to claude_code only.
        _v._set_active_license({"tier": "pro", "limits": {"engines_allowed": ["claude_code"]}})
        res = run_delegate(engine="codex_cli", prompt="hello", audit=False)
        self.assertFalse(res.ok, "codex_cli must be denied when engines_allowed=[claude_code]")
        self.assertIn("engine-not-allowed-by-license", res.error or "")

    def test_allowed_engine_passes_gate(self):
        # When engines_allowed permits the engine, the license gate must NOT deny.
        # (The call may still fail later for other reasons — we only assert the
        # gate did not produce its own deny error.)
        _v._set_active_license({"tier": "pro", "limits": {"engines_allowed": ["codex_cli"]}})
        res = run_delegate(engine="codex_cli", prompt="hello", audit=False, budget_s=1)
        self.assertNotIn("engine-not-allowed-by-license", res.error or "")

    def test_no_limit_allows_any_engine(self):
        # Free/default tier (engines_allowed absent → None) must not deny.
        _v._set_active_license(None)
        res = run_delegate(engine="codex_cli", prompt="hello", audit=False, budget_s=1)
        self.assertNotIn("engine-not-allowed-by-license", res.error or "")


if __name__ == "__main__":
    unittest.main()
