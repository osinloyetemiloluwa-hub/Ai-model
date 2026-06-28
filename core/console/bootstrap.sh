#!/usr/bin/env bash
# bootstrap.sh — set up the console-UI plugin venv.
#
# Idempotent. The console plugin is OPT-IN; single-operator
# deployments that never bootstrap this tree see zero changes to
# the gateway's behaviour (the gateway's app.py wraps
# `from corvin_console import ...` in a try/except).

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

VENV_DIR=".venv"

if [ ! -d "${VENV_DIR}" ]; then
  echo "[bootstrap] creating venv at $(pwd)/${VENV_DIR}"
  python3 -m venv "${VENV_DIR}"
fi

echo "[bootstrap] upgrading pip"
"${VENV_DIR}/bin/pip" install --quiet --upgrade pip

echo "[bootstrap] installing core dependencies"
# uvicorn[standard] pulls websockets + wsproto + httptools + watchfiles +
# uvloop — WebSocket support is REQUIRED for the ADR-0037 web-chat
# channel (routes/chat.py). Without it the WS upgrade fails with a
# silent 403 and the messenger UI shows "WebSocket not connected".
"${VENV_DIR}/bin/pip" install --quiet \
  fastapi pydantic httpx "uvicorn[standard]" itsdangerous python-multipart \
  pyyaml "PyJWT[crypto]" cryptography pytest

echo "[bootstrap] installing voice dependencies (TTS/STT, no API key required for edge-tts)"
# edge-tts: Microsoft Edge TTS, no API key, pure-Python — universal TTS fallback.
# openai + anthropic: SDK for cloud TTS/STT and Claude engine.
"${VENV_DIR}/bin/pip" install --quiet "edge-tts>=6.1.8" "openai>=1.0" "anthropic>=0.25"

# faster-whisper: local STT, no API key, works offline. Not available on
# Windows with Python 3.12+ (missing `av` wheel). On Windows, STT falls
# back to OpenAI Whisper (API key required) — set OPENAI_API_KEY in settings.
if [[ "$(uname -s 2>/dev/null || echo Windows)" != "MINGW"* ]] \
   && [[ "$(uname -s 2>/dev/null || echo Windows)" != "CYGWIN"* ]] \
   && [[ "${OS:-}" != "Windows_NT" ]]; then
  echo "[bootstrap] installing faster-whisper (local STT, Linux/macOS only)"
  "${VENV_DIR}/bin/pip" install --quiet "faster-whisper>=1.0.0" \
    && echo "[bootstrap]   → faster-whisper installed (offline voice input ready)" \
    || echo "[bootstrap]   ! faster-whisper install failed — STT will use OpenAI Whisper (API key needed)"
else
  echo "[bootstrap] skipping faster-whisper on Windows — use OpenAI Whisper instead (set OPENAI_API_KEY)"
fi

echo "[bootstrap] versions:"
"${VENV_DIR}/bin/python" -c "
import fastapi, pydantic, httpx, uvicorn, itsdangerous, yaml, jwt, cryptography
print(f'  fastapi       {fastapi.__version__}')
print(f'  pydantic      {pydantic.VERSION}')
print(f'  httpx         {httpx.__version__}')
print(f'  uvicorn       {uvicorn.__version__}')
print(f'  itsdangerous  {itsdangerous.__version__}')
print(f'  pyyaml        {yaml.__version__}')
print(f'  PyJWT         {jwt.__version__}')
print(f'  cryptography  {cryptography.__version__}')
"
"${VENV_DIR}/bin/python" -c "
import importlib.util
for pkg, mod in [('edge-tts','edge_tts'),('openai','openai'),('faster-whisper','faster_whisper')]:
    found = importlib.util.find_spec(mod) is not None
    print(f'  {pkg:20s} {\"✓\" if found else \"✗ (not installed)\"}')
"

# ── ADR-0037 web-next frontend (optional) ────────────────────────────
#
# If web-next/package.json exists AND `npm` is on PATH AND the operator
# hasn't opted out (CORVIN_SKIP_WEBNEXT_BUILD=1), build the new SPA.
# Failure here is non-fatal — the backend falls back to legacy web/.
WEBNEXT_DIR="corvin_console/web-next"
if [ -f "${WEBNEXT_DIR}/package.json" ] \
   && [ -z "${CORVIN_SKIP_WEBNEXT_BUILD:-}" ] \
   && command -v npm >/dev/null 2>&1; then
  echo "[bootstrap] building web-next/ frontend (ADR-0037)"
  (
    cd "${WEBNEXT_DIR}"
    # Prefer `npm ci` when a lockfile exists for reproducibility.
    if [ -f "package-lock.json" ]; then
      npm ci --silent
    else
      npm install --silent
    fi
    npm run build --silent
  ) && echo "[bootstrap]   → web-next/dist/ built" \
    || echo "[bootstrap]   ! web-next build failed — backend will fall back to legacy web/"
else
  echo "[bootstrap] skipping web-next build (no package.json, no npm, or opt-out)"
fi

echo "[bootstrap] ok"
