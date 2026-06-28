#!/usr/bin/env bash
# bootstrap.sh — set up the corvin-compute venv.
#
# Phase 13.1 ships a stdlib-only venv. Phase 13.8 adds sklearn + numpy
# for the Bayesian strategy; operators on disk-constrained hosts can
# opt out via `CORVIN_COMPUTE_MINIMAL=1`.
#
# Why a venv at all
# -----------------
# Same reasoning as core/gateway: Ubuntu 24.04 ships Python
# under PEP-668 (externally managed). The minimum subset is stdlib-only,
# so a venv is only structurally needed once sklearn lands — but we
# create it from 13.1 so the run-all-tests skip-gate has a single
# stable detection path (`.venv/bin/python`).
#
# Usage
# -----
#   bash core/compute/bootstrap.sh
#   CORVIN_COMPUTE_MINIMAL=1 bash core/compute/bootstrap.sh

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

VENV_DIR=".venv"

if [ ! -d "${VENV_DIR}" ]; then
  echo "[bootstrap] creating venv at $(pwd)/${VENV_DIR}"
  python3 -m venv "${VENV_DIR}"
fi

echo "[bootstrap] upgrading pip"
"${VENV_DIR}/bin/pip" install --quiet --upgrade pip

if [[ "${CORVIN_COMPUTE_MINIMAL:-0}" == "1" ]]; then
  echo "[bootstrap] minimal install (no sklearn) — Bayesian strategy disabled"
  if [ -s requirements-minimal.txt ]; then
    "${VENV_DIR}/bin/pip" install --quiet -r requirements-minimal.txt
  fi
else
  echo "[bootstrap] full install (sklearn + numpy for Bayesian)"
  if [ -s requirements.txt ]; then
    "${VENV_DIR}/bin/pip" install --quiet -r requirements.txt
  fi
fi

echo "[bootstrap] versions:"
"${VENV_DIR}/bin/python" -c "
import sys
print(f'  python      {sys.version.split()[0]}')
try:
    import sklearn
    print(f'  scikit-learn {sklearn.__version__}')
except ImportError:
    print('  scikit-learn (not installed — minimal mode)')
try:
    import numpy
    print(f'  numpy        {numpy.__version__}')
except ImportError:
    print('  numpy        (not installed — minimal mode)')
"

echo "[bootstrap] ok"
