"""ADR-0178 — the corvin-maintainer CLI (keygen / issue / verify / run)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO / "core" / "console", _REPO / "operator" / "forge"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pytest
from corvin_console.aco import maintainer_cli as CLI  # type: ignore
from corvin_console.aco import maintainer_capability as MC  # type: ignore

pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("CORVIN_MAINTAINER_PUBKEY", "CORVIN_MAINTAINER_CAP",
              "CORVIN_MAINTAINER_PRIV", "CORVIN_INSTANCE_ID"):
        monkeypatch.delenv(k, raising=False)


def test_keygen_writes_0600_priv_and_prints_pub(tmp_path, capsys):
    priv = tmp_path / "k.priv"
    rc = CLI.main(["keygen", "--out-priv", str(priv)])
    out = capsys.readouterr().out
    assert rc == 0
    assert priv.is_file()
    if os.name != "nt":
        assert (priv.stat().st_mode & 0o777) == 0o600
    assert "CORVIN_MAINTAINER_PUBKEY=" in out


def test_issue_then_verify_roundtrip(tmp_path, capsys, monkeypatch):
    priv = tmp_path / "k.priv"
    CLI.main(["keygen", "--out-priv", str(priv)])
    pub = re.search(r"CORVIN_MAINTAINER_PUBKEY=(\S+)", capsys.readouterr().out).group(1)

    CLI.main(["issue", "--priv-file", str(priv), "--instance", "inst-1",
              "--subject", "shumway"])
    tok = capsys.readouterr().out.strip().splitlines()[-1].strip()

    monkeypatch.setenv("CORVIN_MAINTAINER_PUBKEY", pub)
    monkeypatch.setenv("CORVIN_MAINTAINER_CAP", tok)
    monkeypatch.setenv("CORVIN_INSTANCE_ID", "inst-1")
    rc = CLI.main(["verify"])
    v = json.loads(capsys.readouterr().out)
    assert rc == 0 and v["allowed"] is True and v["subject"] == "shumway"


def _git_repo(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    def g(*a): subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    g("init", "-b", "main"); g("config", "user.email", "t@t"); g("config", "user.name", "t")
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    g("add", "-A"); g("commit", "-m", "init")
    return repo


def test_run_denied_without_capability(tmp_path):
    repo = _git_repo(tmp_path)
    patch = tmp_path / "p.json"
    patch.write_text(json.dumps({"summary": "s", "risk_class": "platform_path",
                                 "edits": [{"path": "app.py", "new_content": "x=2\n"}]}))
    rc = CLI.main(["run", "--repo", str(repo), "--patch", str(patch),
                   "--test-cmd", "true", "--direct-main"])
    assert rc == 1   # no pinned key / token → denied


def test_run_merges_low_risk_with_capability(tmp_path, capsys, monkeypatch):
    from cryptography.hazmat.primitives import serialization as S
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    sk = Ed25519PrivateKey.generate()
    priv = sk.private_bytes(S.Encoding.Raw, S.PrivateFormat.Raw, S.NoEncryption())
    pub = sk.public_key().public_bytes(S.Encoding.Raw, S.PublicFormat.Raw)
    import base64
    monkeypatch.setenv("CORVIN_INSTANCE_ID", "inst-9")
    monkeypatch.setenv("CORVIN_MAINTAINER_PUBKEY", base64.b64encode(pub).decode())
    monkeypatch.setenv("CORVIN_MAINTAINER_CAP",
                       MC.issue(priv, instance_id="inst-9", subject="shumway"))

    repo = _git_repo(tmp_path)
    patch = tmp_path / "p.json"
    patch.write_text(json.dumps({"summary": "platform path fix",
                                 "risk_class": "platform_path",
                                 "edits": [{"path": "app.py", "new_content": "x = 42\n"}]}))
    rc = CLI.main(["run", "--repo", str(repo), "--patch", str(patch),
                   "--test-cmd", "true", "--direct-main"])
    res = json.loads(capsys.readouterr().out)
    assert rc == 0 and res["status"] == "merged", res
    head = subprocess.run(["git", "-C", str(repo), "show", "main:app.py"],
                          capture_output=True, text=True).stdout
    assert "x = 42" in head


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
