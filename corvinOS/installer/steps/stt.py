"""Faster-Whisper local STT: install speech-to-text engine."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .dependencies import pip_install as _pip_install


def ensure_stt(voice_config_dir: Path, interactive: bool = True) -> None:
    """Ensure STT is available. On Linux/macOS: faster-whisper is now a base dep
    (sys_platform != win32 marker in pyproject.toml) so this is a no-op for most
    installs. On Windows: inform user that STT uses OpenAI Whisper (API key needed)
    or can be enabled manually."""
    import sys as _sys
    try:
        import faster_whisper  # noqa: F401
        print("  ✓ faster-whisper available (local STT, no API key needed)")
        return
    except ImportError:
        pass

    if _sys.platform == "win32":
        print("  ℹ STT on Windows: faster-whisper is not available (missing av wheel).")
        print("    Voice input works with OpenAI Whisper — set OPENAI_API_KEY in the")
        print("    web UI settings to enable it. No key needed for text-only chat.")
        return

    # Linux/macOS — faster-whisper should have been installed as a base dep.
    # If missing, try to install it now (e.g. if user used --no-deps).
    print("  Installing faster-whisper for local voice input (no API key needed)...")
    installed = _pip_install("faster-whisper")
    try:
        import faster_whisper  # noqa: F401
        print("  ✓ faster-whisper installed")
    except ImportError:
        if installed:
            print("  ⚠ faster-whisper installed but not importable — re-open your shell")
        else:
            print("  ⚠ Could not install faster-whisper. Voice input needs OpenAI API key.")
            print("    Manual fix: pip install faster-whisper")


# ── Install ────────────────────────────────────────────────────────────────

def _install_faster_whisper(interactive: bool) -> None:
    """Install faster-whisper via pip if not already available."""
    try:
        import faster_whisper  # noqa: F401
        print("  ✓ faster-whisper already installed")
        return
    except ImportError:
        pass

    # faster-whisper pulls `av` which has no cp312/cp313 Windows wheel; skip and
    # direct users to the OpenAI Whisper cloud provider instead.
    if sys.platform == "win32":
        print("  ℹ faster-whisper is not available on Windows with Python 3.12+ (missing av wheel).")
        print("    Set CORVIN_STT_PROVIDER=openai_whisper to use the OpenAI cloud STT instead.")
        return

    print()
    print("  Faster-Whisper is a local speech-to-text engine.")
    print("  It activates automatically for voice-note transcription.")
    print("  No API key required — works offline.")

    if not interactive:
        print("  Skipping — run corvin-install in a terminal to set up STT.")
        return

    answer = input("  Install Faster-Whisper? [Y/n]: ").strip().lower() or "y"
    if answer.startswith("n"):
        return

    print("  Installing faster-whisper via pip...")
    installed = _pip_install("faster-whisper")

    # Ensure pip --user bin dir is on PATH for this process
    if sys.platform == "win32":
        import os as _os
        appdata = _os.environ.get("APPDATA", "")
        scripts = str(Path(appdata) / "Python" / "Scripts") if appdata else ""
        if scripts and scripts not in _os.environ.get("PATH", ""):
            _os.environ["PATH"] = scripts + ";" + _os.environ.get("PATH", "")
    else:
        local_bin = str(Path.home() / ".local" / "bin")
        if local_bin not in os.environ.get("PATH", ""):
            os.environ["PATH"] = local_bin + ":" + os.environ.get("PATH", "")

    # Verify install
    try:
        import faster_whisper  # noqa: F401
        print("  ✓ Faster-Whisper installed")
    except ImportError:
        if installed:
            print("  ⚠ faster-whisper installed but not importable — re-open your shell if needed")
        else:
            print("  ⚠ Could not install faster-whisper — install manually: pip install faster-whisper")
