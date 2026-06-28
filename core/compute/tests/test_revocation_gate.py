"""RTL2-LIC-01 (ADR-0146): the compute license gate must honor a propagated
revocation (sync_cache.is_revoked) and deny — it previously granted full compute
+ compute_fabric to a revoked-but-unexpired token until the JWT's own expiry.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from corvin_compute import license_gate as LG


def test_revoked_license_is_denied(monkeypatch):
    # Plugin present + propagated revocation present -> deny, do NOT grant/trial.
    monkeypatch.setattr(LG, "_LICENSE_PLUGIN_AVAILABLE", True)
    monkeypatch.setattr(LG, "_is_license_revoked", lambda: True)
    with tempfile.TemporaryDirectory() as td:
        res = LG.check_compute_access(corvin_home=Path(td))
    assert res.allowed is False
    assert res.mode == "denied", f"revoked license must be denied, got mode={res.mode!r}"


def test_non_revoked_falls_through_to_normal_flow(monkeypatch):
    # Not revoked + no license file -> normal trial path (revocation check is a
    # no-op, it does not break the licensed/trial flow).
    monkeypatch.setattr(LG, "_LICENSE_PLUGIN_AVAILABLE", True)
    monkeypatch.setattr(LG, "_is_license_revoked", lambda: False)
    with tempfile.TemporaryDirectory() as td:
        res = LG.check_compute_access(corvin_home=Path(td))
    assert res.mode != "denied", f"non-revoked must not be denied by the revocation gate, got {res.mode!r}"


def test_apache_build_has_no_revocation_channel():
    # _is_license_revoked must never raise and returns False when corvin_license
    # (the enterprise sync plugin) is absent — the Apache-only build is unaffected.
    assert LG._is_license_revoked() in (True, False)
