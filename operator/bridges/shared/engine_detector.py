"""Engine auto-detection for zero-friction onboarding — ADR-0120.

Probes all known CorvinOS engines for availability on the local system.
Reuses the same probe primitives as self_test.py but returns structured
EngineProbe dataclass objects instead of CheckResult entries.

MUST NOT import anthropic (CI AST lint enforces).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field

# ── EngineProbe dataclass ──────────────────────────────────────────────────


@dataclass
class EngineProbe:
    engine_id: str          # "claude_code" | "codex" | "opencode" | "hermes" | "copilot"
    found: bool
    version: str            # empty string when not found
    detail: str             # human-readable note
    locality: str           # "local" | "us_cloud" | "eu_cloud" (from L34 matrix)
    capabilities: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Locality / capability registry ────────────────────────────────────────

_ENGINE_LOCALITY: dict[str, str] = {
    "claude_code": "us_cloud",
    "codex":       "us_cloud",
    "opencode":    "eu_cloud",  # provider-agnostic; local-capable via Ollama
    "hermes":      "local",
    "copilot":     "us_cloud",
}

# Capabilities per engine (from L22 CLAUDE.md)
_ENGINE_CAPABILITIES: dict[str, list[str]] = {
    "claude_code": ["os_turn", "worker", "mid_stream_inject", "hooks", "skills"],
    "codex":       ["worker"],
    "opencode":    ["os_turn", "worker"],
    "hermes":      ["os_turn", "worker"],
    "copilot":     ["worker"],
}

_OLLAMA_PROBE_TIMEOUT = 2.0


# ── Individual probes ──────────────────────────────────────────────────────

def _probe_executable(name: str) -> tuple[bool, str]:
    """Return (found, version_or_detail). Uses --version with a 5 s timeout."""
    exe = shutil.which(name)
    if exe is None:
        return False, f"{name} not on PATH"
    try:
        r = subprocess.run(
            [exe, "--version"], capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return False, f"{name} --version rc={r.returncode}"
        lines = (r.stdout or r.stderr).strip().splitlines()
        version = lines[0] if lines else "found"
        return True, version
    except subprocess.TimeoutExpired:
        return False, f"{name} --version timeout"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def _probe_ollama() -> tuple[bool, str]:
    """Probe Ollama HTTP API. Returns (reachable, detail)."""
    base = (
        os.environ.get("CORVIN_OLLAMA_BASE_URL")
        or os.environ.get("OLLAMA_HOST")
        or "http://localhost:11434"
    ).rstrip("/")
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=_OLLAMA_PROBE_TIMEOUT) as resp:
            data = json.loads(resp.read())
        count = len(data.get("models") or [])
        return True, f"ollama running, {count} model(s) available"
    except urllib.error.URLError:
        return False, "ollama not reachable at localhost:11434"
    except Exception as e:  # noqa: BLE001
        return False, f"ollama probe error: {type(e).__name__}"


# ── Public API ─────────────────────────────────────────────────────────────

def detect_all() -> list[EngineProbe]:
    """Probe all known engines and return EngineProbe objects.

    Never raises — all errors are captured in the detail field.
    """
    probes: list[EngineProbe] = []

    # ClaudeCodeEngine — `claude` binary
    found, detail = _probe_executable("claude")
    probes.append(EngineProbe(
        engine_id="claude_code",
        found=found,
        version=detail if found else "",
        detail=detail,
        locality=_ENGINE_LOCALITY["claude_code"],
        capabilities=list(_ENGINE_CAPABILITIES["claude_code"]),
    ))

    # CodexCliEngine — `codex` binary
    found, detail = _probe_executable("codex")
    probes.append(EngineProbe(
        engine_id="codex",
        found=found,
        version=detail if found else "",
        detail=detail,
        locality=_ENGINE_LOCALITY["codex"],
        capabilities=list(_ENGINE_CAPABILITIES["codex"]),
    ))

    # OpenCodeEngine — `opencode` binary
    found, detail = _probe_executable("opencode")
    probes.append(EngineProbe(
        engine_id="opencode",
        found=found,
        version=detail if found else "",
        detail=detail,
        locality=_ENGINE_LOCALITY["opencode"],
        capabilities=list(_ENGINE_CAPABILITIES["opencode"]),
    ))

    # HermesEngine — Ollama HTTP API (no dedicated binary; probed via API)
    found, detail = _probe_ollama()
    probes.append(EngineProbe(
        engine_id="hermes",
        found=found,
        version="",  # version not exposed by /api/tags
        detail=detail,
        locality=_ENGINE_LOCALITY["hermes"],
        capabilities=list(_ENGINE_CAPABILITIES["hermes"]),
    ))

    # CopilotCliEngine — `copilot` binary (ADR-0071)
    found, detail = _probe_executable("copilot")
    probes.append(EngineProbe(
        engine_id="copilot",
        found=found,
        version=detail if found else "",
        detail=detail,
        locality=_ENGINE_LOCALITY["copilot"],
        capabilities=list(_ENGINE_CAPABILITIES["copilot"]),
    ))

    return probes
