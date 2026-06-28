"""Phase E E2E: bridge events land in the hash-chained audit log.

Three scenarios:

  1. A fictional Discord whitelist denial + PIN failure + persona-routed
     sequence is written through audit_event(). The chain verifies.
     `voice-audit verify` exits 0 and prints "audit OK".

  2. An attacker mutates one entry's tool field but leaves the hash
     untouched. `voice-audit verify` exits 1 and pinpoints the line.

  3. Forge plugin removed at runtime → audit_event becomes a no-op
     instead of crashing the bridge.

The bridge layer never has to call write_event directly — it goes
through ``bridges/shared/audit.audit_event(...)``, which is the public
contract that the adapter / daemons rely on.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "operator" / "bridges" / "shared"))

# The voice audit module — this is the public surface the bridge code uses.
import audit as _voice_audit  # noqa: E402


PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


# ---------- (1) — happy path through the bridge surface ------------------

def test_bridge_events_chain_verifies_clean():
    print("\n[bridge events → hash-chain → voice-audit verify exits 0]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        audit_path = td / "audit.jsonl"
        env = dict(os.environ); env["VOICE_AUDIT_PATH"] = str(audit_path)
        os.environ["VOICE_AUDIT_PATH"] = str(audit_path)
        try:
            # A realistic mini-sequence:
            _voice_audit.audit_event(
                "bridge.message_received",
                channel="discord", chat_key="cid:42",
                user="123456789", persona="forge",
                details={"text_len": 42},
            )
            _voice_audit.audit_event(
                "bridge.whitelist_deny",
                channel="discord", chat_key="cid:42",
                user="hostile_id",
                details={"reason": "not in whitelist"},
            )
            _voice_audit.audit_event(
                "bridge.persona_routed",
                channel="discord", chat_key="cid:42",
                user="123456789", persona="forge",
                details={"confidence": 0.91, "via": "embedding"},
            )

            t("audit file exists", audit_path.exists())
            lines = audit_path.read_text().splitlines()
            t("3 events written", len(lines) == 3)
            recs = [json.loads(l) for l in lines]
            types = [r["event_type"] for r in recs]
            t("event_types match",
              types == ["bridge.message_received",
                        "bridge.whitelist_deny",
                        "bridge.persona_routed"])
            t("each record has hash + prev_hash",
              all("hash" in r and "prev_hash" in r for r in recs))
            t("chain is properly linked (prev_hash chains)",
              recs[0]["prev_hash"] == ""
              and recs[1]["prev_hash"] == recs[0]["hash"]
              and recs[2]["prev_hash"] == recs[1]["hash"])

            # Verify via Python API
            ok, problems = _voice_audit.verify_audit(audit_path)
            t("verify_audit() ok", ok and not problems)

            # Verify via the CLI
            cli_path = REPO_ROOT / "operator" / "voice" / "scripts" / "voice_audit.py"
            proc = subprocess.run(
                [sys.executable, str(cli_path), "--path",
                 str(audit_path), "verify"],
                capture_output=True, text=True,
            )
            t("CLI rc=0", proc.returncode == 0)
            t("CLI says 'audit OK'", "audit OK" in proc.stdout)

            # tail should print 3 lines
            proc = subprocess.run(
                [sys.executable, str(cli_path), "--path",
                 str(audit_path), "tail", "--limit", "5"],
                capture_output=True, text=True,
            )
            t("tail rc=0", proc.returncode == 0)
            t("tail mentions all three event types",
              all(et in proc.stdout for et in
                  ("bridge.message_received",
                   "bridge.whitelist_deny",
                   "bridge.persona_routed")))
        finally:
            os.environ.pop("VOICE_AUDIT_PATH", None)


# ---------- (2) — tampering is caught, line number reported --------------

def test_tampered_audit_fails_verify():
    print("\n[mutate one entry's tool field → CLI exits 1, names the line]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        audit_path = td / "audit.jsonl"
        os.environ["VOICE_AUDIT_PATH"] = str(audit_path)
        try:
            for i in range(4):
                _voice_audit.audit_event(
                    "bridge.message_received",
                    channel="discord", chat_key=f"cid:{i}",
                    user="u", persona="coder",
                )
            # Tamper line 2: change persona, leave hash alone
            lines = audit_path.read_text().splitlines()
            rec = json.loads(lines[1])
            rec["details"]["persona"] = "evil_persona"
            lines[1] = json.dumps(rec)
            audit_path.write_text("\n".join(lines) + "\n")

            cli = REPO_ROOT / "operator" / "voice" / "scripts" / "voice_audit.py"
            proc = subprocess.run(
                [sys.executable, str(cli), "--path",
                 str(audit_path), "verify"],
                capture_output=True, text=True,
            )
            t("CLI rc=1", proc.returncode == 1)
            t("stderr says INTEGRITY VIOLATION",
              "INTEGRITY VIOLATION" in proc.stderr)
            t("stderr names line 2",
              "line 2" in proc.stderr)
            t("stderr names 'tampered' issue",
              "tampered" in proc.stderr)
        finally:
            os.environ.pop("VOICE_AUDIT_PATH", None)


# ---------- (3) — graceful no-op when forge is absent --------------------

def test_audit_silent_noop_when_forge_missing(monkeypatch=None):
    print("\n[forge not importable → audit_event is a silent no-op, verify=ok]")
    # Simulate by reloading audit.py with _se forced to None
    import importlib
    saved = sys.modules.get("audit")
    try:
        # Reload with forge import shim broken
        sys.modules.pop("audit", None)
        # We can't easily prevent the import path — instead, after
        # import, monkey-patch _se to None and validate the API contract.
        import audit as audit_mod  # noqa: F401
        importlib.reload(audit_mod)
        old_se = audit_mod._se
        audit_mod._se = None
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            os.environ["VOICE_AUDIT_PATH"] = str(td / "audit.jsonl")
            try:
                # No exception, no file created
                audit_mod.audit_event(
                    "bridge.login",
                    channel="discord", user="x",
                )
                t("no audit file written when forge missing",
                  not (td / "audit.jsonl").exists())
                ok, problems = audit_mod.verify_audit()
                t("verify returns (True, []) when forge missing",
                  ok and problems == [])
            finally:
                os.environ.pop("VOICE_AUDIT_PATH", None)
                audit_mod._se = old_se
    finally:
        if saved is not None:
            sys.modules["audit"] = saved


def main() -> int:
    test_bridge_events_chain_verifies_clean()
    test_tampered_audit_fails_verify()
    test_audit_silent_noop_when_forge_missing()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
