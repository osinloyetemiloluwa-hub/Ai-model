"""Regression: the corvin-webui systemd unit MUST pin CORVIN_HOME explicitly.

Root cause of the "no worker graph in Chat Audits" report: corvin_home() was
resolved per-process by cwd-walk, so the console (reader) and the adapter / ACS
(writers) could resolve different homes — ACS runs + worker-audit then landed in
a home the console never read, leaving the WDAT worker graph empty.

The fix pins CORVIN_HOME in the unit template (and operator service.env). This
test locks the template invariant so the fragility can't silently return.
"""
from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_UNIT = _REPO / "core" / "gateway" / "systemd" / "corvin-webui.service"


def test_webui_unit_template_exists() -> None:
    assert _UNIT.is_file(), f"missing unit template: {_UNIT}"


def test_webui_unit_pins_corvin_home() -> None:
    text = _UNIT.read_text(encoding="utf-8")
    # Must set CORVIN_HOME explicitly (not rely on cwd-walk resolution).
    assert "Environment=CORVIN_HOME=" in text, (
        "corvin-webui.service must pin CORVIN_HOME explicitly so the console "
        "reader and ACS writers resolve the SAME runtime root (else the WDAT "
        "worker graph silently goes empty when homes diverge)."
    )
    # The pin must use the install-time repo-root substitution, not a hardcode.
    assert "Environment=CORVIN_HOME=__REPO_ROOT__/.corvin" in text, (
        "CORVIN_HOME must be __REPO_ROOT__/.corvin so install-systemd path "
        "substitution resolves it deterministically."
    )


def test_webui_unit_still_loads_operator_env_file() -> None:
    # service.env (operator-owned) may override CORVIN_HOME; it must stay loaded
    # AFTER the Environment= line so a deliberate operator choice wins, and so
    # the same home reaches every unit that loads it.
    text = _UNIT.read_text(encoding="utf-8")
    assert "EnvironmentFile=-%h/.config/corvin-voice/service.env" in text
