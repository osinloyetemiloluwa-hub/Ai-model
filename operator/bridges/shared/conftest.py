"""conftest.py — pytest session fixtures for operator/bridges/shared/ tests.

Autouse fixture: reset CLAG shadow hashes before each test so that
cross-test shadow contamination does not produce false ChainIntegrityFailure
failures in tests that call consent.is_granted() or disclosure.mark_seen()
and share the "L16.consent_gate" / "L19.disclosure_gate" layer_id.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_shared_dir = Path(__file__).resolve().parent
if str(_shared_dir) not in sys.path:
    sys.path.insert(0, str(_shared_dir))

_forge_inner = Path(__file__).resolve().parents[2] / "forge" / "forge"
if str(_forge_inner) not in sys.path:
    sys.path.insert(0, str(_forge_inner))

try:
    from clag import clear_shadow_hashes as _clear_shadows  # type: ignore
    _HAS_CLAG = True
except ImportError:
    _HAS_CLAG = False


@pytest.fixture(autouse=True)
def _reset_clag_shadows():
    """Clear CLAG per-layer shadow hashes before and after every test."""
    if _HAS_CLAG:
        _clear_shadows()
    yield
    if _HAS_CLAG:
        _clear_shadows()
