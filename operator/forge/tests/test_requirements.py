"""Tests for meta.requirements — per-tool dependency installation.

Covers:
  - ensure_requirements() returns None for empty list
  - cache key is stable and version-scoped
  - pip is called with the right arguments on a cache miss
  - pip output is NOT re-run when the sentinel exists (cache hit)
  - req_site_dir is bound into bwrap cmd and into PYTHONPATH
  - non-bwrap path injects PYTHONPATH in env dict
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from forge.sandbox import (
    _reqs_cache_key,
    build_bwrap_cmd,
    ensure_requirements,
)


# ── _reqs_cache_key ──────────────────────────────────────────────────────────

def test_cache_key_is_stable():
    k1 = _reqs_cache_key(["matplotlib>=3.6", "pandas"])
    k2 = _reqs_cache_key(["pandas", "matplotlib>=3.6"])
    assert k1 == k2, "key must be order-independent"


def test_cache_key_differs_by_content():
    k1 = _reqs_cache_key(["matplotlib"])
    k2 = _reqs_cache_key(["pandas"])
    assert k1 != k2


def test_cache_key_length():
    k = _reqs_cache_key(["matplotlib"])
    assert len(k) == 16, "expected 16-char hex digest"


# ── ensure_requirements ───────────────────────────────────────────────────────

def test_empty_requirements_returns_none():
    assert ensure_requirements([], Path("/tmp")) is None
    assert ensure_requirements(None, Path("/tmp")) is None  # type: ignore[arg-type]


def test_sentinel_prevents_pip_rerun():
    """When .installed sentinel exists, pip must NOT be called."""
    with tempfile.TemporaryDirectory() as td:
        cache_root = Path(td)
        reqs = ["matplotlib>=3.6"]
        key = _reqs_cache_key(reqs)
        target = cache_root / "req_cache" / key
        target.mkdir(parents=True)
        (target / ".installed").touch()

        with mock.patch("forge.sandbox.subprocess.run") as mock_pip:
            result = ensure_requirements(reqs, cache_root)

        assert result == target
        mock_pip.assert_not_called()


def test_pip_called_on_cache_miss():
    """On a cache miss, pip install --target must be invoked."""
    with tempfile.TemporaryDirectory() as td:
        cache_root = Path(td)
        reqs = ["somepkg==1.0"]

        mock_result = mock.MagicMock()
        mock_result.returncode = 0

        with mock.patch("forge.sandbox.subprocess.run", return_value=mock_result) as mock_pip:
            result = ensure_requirements(reqs, cache_root)

        assert result is not None
        call_args = mock_pip.call_args[0][0]  # first positional arg = cmd list
        assert "--target" in call_args
        assert "somepkg==1.0" in call_args
        assert "--quiet" in call_args
        assert "--no-warn-script-location" in call_args
        # Sentinel written after success
        assert (result / ".installed").exists()


def test_pip_failure_returns_none(capsys):
    """When pip exits non-zero, ensure_requirements returns None (fail-soft)."""
    with tempfile.TemporaryDirectory() as td:
        cache_root = Path(td)
        reqs = ["nonexistent-package-xyz==9.9.9"]

        mock_result = mock.MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "ERROR: Could not find a version..."

        with mock.patch("forge.sandbox.subprocess.run", return_value=mock_result):
            result = ensure_requirements(reqs, cache_root)

        assert result is None


# ── build_bwrap_cmd with extra_pythonpath ─────────────────────────────────────

def _dummy_impl(tmp: Path) -> Path:
    p = tmp / "tool.py"
    p.write_text("print('hello')")
    return p


def test_bwrap_injects_pythonpath():
    """extra_pythonpath shows up as --setenv PYTHONPATH in bwrap args."""
    with tempfile.TemporaryDirectory() as td:
        impl = _dummy_impl(Path(td))
        req_dir = Path(td) / "req_cache" / "abc123"
        req_dir.mkdir(parents=True)

        cmd = build_bwrap_cmd(
            [sys.executable, str(impl)],
            impl,
            extra_pythonpath=[req_dir],
        )

        # Find the --setenv PYTHONPATH pair (there may also be --setenv TMPDIR)
        found = False
        for i, tok in enumerate(cmd):
            if tok == "--setenv" and i + 2 < len(cmd) and cmd[i + 1] == "PYTHONPATH":
                assert str(req_dir) in cmd[i + 2]
                found = True
                break
        assert found, f"--setenv PYTHONPATH not found in bwrap cmd: {cmd}"


def test_bwrap_combines_pythonpath_with_deny_loopback():
    """deny_loopback + extra_pythonpath => single PYTHONPATH with both dirs."""
    with tempfile.TemporaryDirectory() as td:
        impl = _dummy_impl(Path(td))
        req_dir = Path(td) / "req_cache" / "abc123"
        req_dir.mkdir(parents=True)

        from forge.sandbox import SANDBOX_HELPERS_DIR
        if not SANDBOX_HELPERS_DIR.is_dir():
            return  # sandbox_helpers not present in this env — skip

        cmd = build_bwrap_cmd(
            [sys.executable, str(impl)],
            impl,
            allow_network=True,
            deny_loopback=True,
            extra_pythonpath=[req_dir],
        )

        idx = cmd.index("PYTHONPATH")
        combined = cmd[idx + 1]
        assert str(req_dir) in combined
        assert str(SANDBOX_HELPERS_DIR) in combined


def test_bwrap_no_pythonpath_when_no_extras():
    """Without deny_loopback and no extra_pythonpath, no --setenv PYTHONPATH."""
    with tempfile.TemporaryDirectory() as td:
        impl = _dummy_impl(Path(td))
        cmd = build_bwrap_cmd(
            [sys.executable, str(impl)],
            impl,
        )
        # There may be a --setenv TMPDIR but not PYTHONPATH
        pairs = list(zip(cmd, cmd[1:]))
        pypath_pairs = [(a, b) for a, b in pairs if a == "--setenv" and b == "PYTHONPATH"]
        assert not pypath_pairs


# ── runner integration: PYTHONPATH in non-bwrap env ──────────────────────────

def test_runner_sets_pythonpath_in_nonbwrap_env():
    """When bwrap is absent, req_site_dir must appear in the env dict."""
    import os
    from unittest.mock import patch, MagicMock
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        from forge.registry import Registry

        reg_root = Path(td) / "forge"
        reg = Registry(reg_root)

        impl_code = (
            "import json, sys; d=json.load(sys.stdin); "
            "print(json.dumps({'ok':True,'status':200,'data':'ok',"
            "'error':None,'meta':{}}))"
        )
        reg.create(
            name="test_req_tool",
            description="test",
            input_schema={"type": "object", "properties": {}},
            impl=impl_code,
            runtime="python",
            meta={"requirements": ["urllib3"]},
        )

        req_fake = Path(td) / "req_cache" / "fakehash"
        req_fake.mkdir(parents=True)

        launched_env: dict = {}

        orig_popen = __import__("subprocess").Popen

        def fake_popen(cmd, **kwargs):
            nonlocal launched_env
            launched_env = dict(kwargs.get("env") or {})
            # Return a minimal stub
            m = MagicMock()
            m.communicate.return_value = (
                b'{"ok":true,"status":200,"data":"ok","error":null,"meta":{}}',
                b"",
            )
            m.returncode = 0
            return m

        from forge import runner as _runner_mod
        with patch.object(_runner_mod, "have_bwrap", return_value=False), \
             patch.object(_runner_mod, "ensure_requirements", return_value=req_fake), \
             patch("subprocess.Popen", side_effect=fake_popen):
            from forge.runner import run_tool
            run_tool(
                reg,
                "test_req_tool",
                {},
                permission_mode="yes",
                use_sandbox=True,
            )

        assert "PYTHONPATH" in launched_env
        assert str(req_fake) in launched_env["PYTHONPATH"]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
