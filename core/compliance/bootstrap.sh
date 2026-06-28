#!/usr/bin/env bash
# bootstrap.sh — set up the compliance-reports plugin venv.
#
# Idempotent. Opt-in. Provides PDF generation for EU AI Act Art. 50
# evidence, GDPR Art. 30 RoPA, and Audit-Chain Integrity Attestation.
# Apache-2.0 (free baseline reports must be free — transparency is
# never a paywall).

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

VENV_DIR=".venv"

if [ ! -d "${VENV_DIR}" ]; then
  echo "[bootstrap] creating venv at $(pwd)/${VENV_DIR}"
  python3 -m venv "${VENV_DIR}"
fi

echo "[bootstrap] upgrading pip"
"${VENV_DIR}/bin/pip" install --quiet --upgrade pip

echo "[bootstrap] installing reportlab + pytest"
"${VENV_DIR}/bin/pip" install --quiet \
  "reportlab>=4.0" pytest

echo "[bootstrap] versions:"
"${VENV_DIR}/bin/python" -c "
import reportlab
print(f'  reportlab  {reportlab.Version}')
"

echo "[bootstrap] ok"
