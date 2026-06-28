"""Unit tests for ServiceManager implementations."""

import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from corvinOS.installer.service_manager import (
    LinuxServiceManager,
    DarwinServiceManager,
    WindowsServiceManager,
    get_service_manager,
)


class TestLinuxServiceManager:
    """Tests for Linux systemd manager."""

    def test_init(self):
        """Test initialization creates systemd user dir."""
        mgr = LinuxServiceManager()
        assert mgr.systemd_user_dir.exists()

    def test_service_file_path(self):
        """Test service file path generation."""
        mgr = LinuxServiceManager()
        path = mgr._service_file("test")
        assert "corvin-test.service" in str(path)
        assert ".config/systemd/user" in str(path)

    def test_install_service_creates_unit(self):
        """Test that install_service creates a valid unit file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            mgr = LinuxServiceManager()
            mgr.systemd_user_dir = tmpdir_path

            mgr.install_service(
                name="test",
                command="/usr/bin/echo hello",
                description="Test service",
                auto_start=False,
            )

            unit_file = tmpdir_path / "corvin-test.service"
            assert unit_file.exists()

            content = unit_file.read_text()
            assert "[Unit]" in content
            assert "Description=Test service" in content
            assert "[Service]" in content
            assert "ExecStart=/usr/bin/echo hello" in content
            assert "[Install]" in content

    def test_install_service_permissions(self):
        """Test that unit file has correct permissions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            mgr = LinuxServiceManager()
            mgr.systemd_user_dir = tmpdir_path

            mgr.install_service(
                name="test",
                command="/usr/bin/true",
                auto_start=False,
            )

            unit_file = tmpdir_path / "corvin-test.service"
            # File permissions should be 0o644
            mode = unit_file.stat().st_mode
            assert (mode & 0o644) == 0o644


class TestServiceNameContract:
    """Regression: the installer must register systemd units under the SAME
    names that bridge.sh and steps/validate.py control. service_manager prefixes
    `corvin-`, so the installer passes `voice-bridge-<channel>` (and
    `voice-bridge-adapter`) to land on `corvin-voice-bridge-<channel>.service`.
    A drift here silently orphans every service after install (the original
    `adapter` / `bridge-<x>` names produced `corvin-adapter.service` etc., which
    bridge.sh/validate.py never see)."""

    # Canonical units that bridge.sh manages and validate.py checks.
    CANONICAL = {
        "voice-bridge-adapter": "corvin-voice-bridge-adapter.service",
        "voice-bridge-whatsapp": "corvin-voice-bridge-whatsapp.service",
        "voice-bridge-telegram": "corvin-voice-bridge-telegram.service",
        "voice-bridge-discord": "corvin-voice-bridge-discord.service",
        "voice-bridge-slack": "corvin-voice-bridge-slack.service",
        "voice-bridge-email": "corvin-voice-bridge-email.service",
    }

    def test_service_file_matches_canonical_units(self):
        mgr = LinuxServiceManager()
        for name, unit in self.CANONICAL.items():
            assert mgr._service_file(name).name == unit

    def test_installer_core_uses_voice_bridge_names(self):
        """core.py must pass `voice-bridge-*` names (not the legacy
        `adapter` / `bridge-<x>` that orphaned the units)."""
        core_src = (
            Path(__file__).resolve().parent.parent
            / "corvinOS" / "installer" / "core.py"
        ).read_text()
        assert 'name="voice-bridge-adapter"' in core_src
        assert 'name=f"voice-bridge-{bridge}"' in core_src
        # The legacy names must be gone from service-name call sites.
        assert 'start_service("adapter")' not in core_src
        assert 'start_service(f"bridge-{bridge}")' not in core_src
        assert '["adapter"] + [f"bridge-{b}"' not in core_src

    def test_validate_expected_list_is_covered(self):
        """Every unit steps/validate.py checks must be derivable from a
        `voice-bridge-*` name the installer actually registers."""
        validate_src = (
            Path(__file__).resolve().parent.parent
            / "corvinOS" / "installer" / "steps" / "validate.py"
        ).read_text()
        for unit in self.CANONICAL.values():
            assert unit in validate_src


class TestDarwinServiceManager:
    """Tests for macOS launchd manager."""

    def test_init(self):
        """Test initialization."""
        mgr = DarwinServiceManager()
        # Don't assert directory exists (may not exist on Linux)
        assert mgr.launchagents_dir == Path.home() / "Library" / "LaunchAgents"

    def test_plist_path(self):
        """Test plist path generation."""
        mgr = DarwinServiceManager()
        path = mgr._plist_path("test")
        assert "com.corvin.test.plist" in str(path)

    def test_generate_plist(self):
        """Test plist generation."""
        mgr = DarwinServiceManager()
        plist = mgr._generate_plist("test", "/usr/bin/echo hello", "Test service")

        assert '<?xml version="1.0"' in plist
        assert "<plist version=" in plist
        assert "com.corvin.test" in plist
        assert "<key>Program</key>" in plist
        assert "<string>/usr/bin/echo</string>" in plist
        assert "<key>RunAtLoad</key>" in plist
        assert "<true/>" in plist

    def test_generate_plist_with_args(self):
        """Test plist generation with multiple arguments."""
        mgr = DarwinServiceManager()
        plist = mgr._generate_plist(
            "test", "/usr/bin/python -m mymodule --flag value", "Test"
        )

        assert "<key>ProgramArguments</key>" in plist
        assert "<array>" in plist
        assert "<string>-m</string>" in plist
        assert "<string>mymodule</string>" in plist
        assert "<string>--flag</string>" in plist
        assert "<string>value</string>" in plist


class TestWindowsServiceManager:
    """Tests for Windows Task Scheduler manager."""

    def test_task_name(self):
        """Test task name generation."""
        mgr = WindowsServiceManager()
        name = mgr._task_name("test")
        assert name == "CorvinOS\\test"

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only test")
    def test_install_service_windows(self):
        """Test Task Scheduler service creation on Windows."""
        mgr = WindowsServiceManager()
        # Don't actually install, just verify the method exists
        assert hasattr(mgr, "install_service")


class TestGetServiceManager:
    """Tests for platform detection."""

    def test_get_service_manager_returns_correct_type(self):
        """Test that get_service_manager returns correct type for platform."""
        mgr = get_service_manager()

        if sys.platform == "darwin":
            assert isinstance(mgr, DarwinServiceManager)
        elif sys.platform == "win32":
            assert isinstance(mgr, WindowsServiceManager)
        else:
            assert isinstance(mgr, LinuxServiceManager)

    def test_all_managers_implement_interface(self):
        """Test that all managers implement the required methods."""
        managers = [
            LinuxServiceManager(),
            DarwinServiceManager(),
            WindowsServiceManager(),
        ]

        required_methods = [
            "install_service",
            "start_service",
            "stop_service",
            "enable_autostart",
            "disable_autostart",
            "uninstall_service",
            "status",
            "is_active",
        ]

        for mgr in managers:
            for method in required_methods:
                assert hasattr(mgr, method), f"{mgr.__class__.__name__} missing {method}"
                assert callable(getattr(mgr, method))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
