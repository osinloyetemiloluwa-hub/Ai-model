#!/usr/bin/env bash
# bootstrap.sh — set up the gateway venv with FastAPI + pydantic + httpx + uvicorn.
#
# Idempotent: a second run upgrades pip and reinstalls the deps in place.
# Phase 2.1's bearer-token module runs on pure stdlib and does NOT need
# the venv — only Phase 2.2+ (FastAPI app, TestClient tests, eventual
# uvicorn boot) does.
#
# Why a venv instead of system pip
# --------------------------------
# Ubuntu 24.04 / Debian 12+ ship Python under PEP-668 (externally
# managed). System-wide `pip install` requires --break-system-packages
# which is undesirable. Distro-packaged python3-fastapi exists but
# pins to fastapi 0.101 + pydantic 1.x, which lags the upstream
# v2 contract this plugin uses. Per-plugin venv keeps the dependency
# graph hermetic and isolated from anything the rest of Corvin
# touches.
#
# Usage
# -----
#   bash core/gateway/bootstrap.sh
#
# Test
# ----
#   core/gateway/.venv/bin/python core/gateway/tests/test_app.py

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

VENV_DIR=".venv"

if [ ! -d "${VENV_DIR}" ]; then
  echo "[bootstrap] creating venv at $(pwd)/${VENV_DIR}"
  python3 -m venv "${VENV_DIR}"
fi

echo "[bootstrap] upgrading pip"
"${VENV_DIR}/bin/pip" install --quiet --upgrade pip

echo "[bootstrap] installing FastAPI stack + PyYAML + JWT/crypto + grpcio"
"${VENV_DIR}/bin/pip" install --quiet \
  fastapi pydantic httpx uvicorn pyyaml "PyJWT[crypto]" cryptography \
  grpcio grpcio-tools

echo "[bootstrap] versions:"
"${VENV_DIR}/bin/python" -c "
import fastapi, pydantic, httpx, uvicorn, yaml, jwt, cryptography, grpc
print(f'  fastapi      {fastapi.__version__}')
print(f'  pydantic     {pydantic.VERSION}')
print(f'  httpx        {httpx.__version__}')
print(f'  uvicorn      {uvicorn.__version__}')
print(f'  pyyaml       {yaml.__version__}')
print(f'  PyJWT        {jwt.__version__}')
print(f'  cryptography {cryptography.__version__}')
print(f'  grpcio       {grpc.__version__}')
"

# Generate gRPC stubs from the proto (idempotent — protoc rewrites
# corvin_pb2*.py from corvin.proto each run).
if [ -f corvin_gateway/grpc/corvin.proto ]; then
  echo "[bootstrap] generating gRPC stubs"
  (cd corvin_gateway/grpc && \
   ../../"${VENV_DIR}"/bin/python -m grpc_tools.protoc \
     -I. --python_out=. --grpc_python_out=. corvin.proto)
  # grpcio-tools emits `import corvin_pb2` without the package
  # prefix; patch it to a relative import so the package layout
  # works without messing with sys.path.
  if grep -q '^import corvin_pb2' corvin_gateway/grpc/corvin_pb2_grpc.py; then
    sed -i 's|^import corvin_pb2|from . import corvin_pb2|' \
      corvin_gateway/grpc/corvin_pb2_grpc.py
  fi
fi

echo "[bootstrap] ok"
