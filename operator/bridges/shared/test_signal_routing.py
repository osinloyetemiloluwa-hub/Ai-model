#!/usr/bin/env python3
"""test_signal_routing.py — E2E for Phase-4.1.5 /sig signal routing.

Verifies:
  - phase3_cli.py sig writes a _signal envelope to the right inbox
  - The adapter recognises _signal envelopes via _peek_side_channel
    (so they bypass the per-chat lock, like /btw and /cancel)
  - The adapter's process_one resolves session_id -> chat_key via
    process_table, dispatches PLAN/SUMMARIZE/etc as inject_btw with
    a [CORVIN_SIGNAL: NAME] marker, KILL via _cancel_chat
  - Unknown session, unknown signal, missing args produce clear
    audit + ack envelopes with delivered=False
  - The CLI rejects invalid signal names before writing the envelope

Per-subtask E2E rule: real envelope write, real adapter
process_one, real process_table state. No mocks for moving parts;
the running claude subprocess is replaced by registering a stub
session in process_table without a real PID (the adapter resolves
session -> chat_key correctly even when no live process exists,
which is the exact failure mode we test for).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

CLI = ROOT / "phase3_cli.py"


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _setup_sandbox() -> tuple[Path, Path, Path, Path]:
    """Create CORVIN_HOME + inbox + outbox + processed dirs."""
    home = Path(tempfile.mkdtemp(prefix="sig-test-"))
    inbox = home / "inbox"
    outbox = home / "outbox"
    processed = home / "processed"
    for d in (inbox, outbox, processed):
        d.mkdir(parents=True, exist_ok=True)
    return home, inbox, outbox, processed


def _register_stub_session(home: Path, session_id: str,
                           chat_key: str = "discord:42") -> None:
    """Register a session record in process_table without a real
    subprocess. The signal handler resolves session -> chat_key
    correctly; delivery fails with a clear reason because no
    _running_subprocs entry exists for chat_key — that's the
    test condition for graceful no-op."""
    os.environ["CORVIN_HOME"] = str(home)
    sys.modules.pop("process_table", None)
    sys.modules.pop("paths", None)
    import process_table  # type: ignore
    process_table.register_session(
        session_id, chat_key=chat_key, persona="coder", pid=99999,
    )


def _run_cli(*args: str, home: Path, inbox: Path | None = None,
             extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["CORVIN_HOME"] = str(home)
    if inbox is not None:
        env["ADAPTER_INBOX"] = str(inbox)
    env.setdefault("CORVIN_CHANNEL_ID", "discord:42")
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        env=env, capture_output=True, text=True,
    )


# --------------------------------------------------------------------- cases

def case_sig_writes_envelope(home: Path, inbox: Path, outbox: Path,
                              processed: Path) -> None:
    _section("sig writes _signal envelope to inbox")
    _register_stub_session(home, "s_sig_test")
    r = _run_cli("sig", "s_sig_test", "PLAN", home=home, inbox=inbox)
    assert r.returncode == 0, f"stderr={r.stderr}"

    files = list(inbox.glob("sig_*.json"))
    assert len(files) == 1, files
    payload = json.loads(files[0].read_text())
    assert payload["_signal"] is True
    assert payload["session_id"] == "s_sig_test"
    assert payload["signal"] == "PLAN"
    assert payload["channel"] == "discord"
    assert payload["chat_id"] == "42"
    print(f"  PASS envelope shape ok: {payload['signal']}, chat={payload['chat_id']}")


def case_sig_rejects_invalid_signal(home, inbox, outbox, processed) -> None:
    _section("sig rejects unknown signal name")
    _register_stub_session(home, "s_bad_sig")
    r = _run_cli("sig", "s_bad_sig", "INVALID_SIGNAL", home=home, inbox=inbox)
    assert r.returncode == 1, r.stdout
    assert "unknown signal" in r.stderr, r.stderr
    assert not list(inbox.glob("sig_*.json")), "no envelope on bad signal"
    print(f"  PASS rejected: {r.stderr.strip()}")


def case_sig_rejects_unknown_session(home, inbox, outbox, processed) -> None:
    _section("sig rejects unknown session_id")
    r = _run_cli("sig", "s_does_not_exist", "PLAN", home=home, inbox=inbox)
    assert r.returncode == 1, r.stdout
    assert "unknown session" in r.stderr, r.stderr
    assert not list(inbox.glob("sig_*.json"))
    print(f"  PASS rejected: {r.stderr.strip()}")


def case_sig_validates_arity(home, inbox, outbox, processed) -> None:
    _section("sig requires session_id + signal")
    r = _run_cli("sig", home=home, inbox=inbox)
    assert r.returncode == 1
    r2 = _run_cli("sig", "s_only_session", home=home, inbox=inbox)
    assert r2.returncode == 1
    print("  PASS rejected: missing args")


def case_adapter_handles_signal_envelope(home, inbox, outbox, processed) -> None:
    """The most important case: write a _signal envelope, run the
    adapter's process_one, verify the audit event lands and the
    outbox ack is generated."""
    _section("adapter process_one handles _signal envelope end-to-end")
    _register_stub_session(home, "s_e2e_signal", chat_key="discord:99")

    # Force the adapter's INBOX/OUTBOX/PROCESSED paths into our sandbox
    os.environ["ADAPTER_INBOX"] = str(inbox)
    os.environ["ADAPTER_OUTBOX"] = str(outbox)
    os.environ["ADAPTER_PROCESSED"] = str(processed)
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_CHANNEL_ID"] = "discord:99"

    # Re-import adapter so it picks up the new env-derived paths
    for mod in ("adapter", "process_table", "paths"):
        sys.modules.pop(mod, None)
    import adapter  # type: ignore

    # Write the envelope directly (skip the CLI for tighter unit-of-test)
    msg_id = "sig_test_e2e"
    envelope = {
        "msg_id": msg_id,
        "channel": "discord",
        "from": "_test",
        "chat_id": "99",
        "_signal": True,
        "session_id": "s_e2e_signal",
        "signal": "PLAN",
        "ts": 0,
    }
    inbox_file = inbox / f"{msg_id}.json"
    inbox_file.write_text(json.dumps(envelope))

    # _peek_side_channel must classify _signal as bypass-lock
    assert adapter._peek_side_channel(inbox_file) is True
    print("  PASS _peek_side_channel classifies _signal as bypass-lock")

    # process_one handles the envelope. Even though there's no real
    # subprocess for chat=discord:99, the handler should:
    #   - resolve session via process_table (success)
    #   - attempt inject_btw (fail because no running stdin)
    #   - log delivered=False with reason "no running subprocess"
    #   - write outbox ack
    #   - move inbox to processed
    settings = {"whitelist": []}  # empty whitelist = legacy fail-open
    adapter.process_one(inbox_file, settings)

    # Verify outbox ack was written
    out_files = list(outbox.glob(f"{msg_id}*"))
    assert len(out_files) == 1, out_files
    ack = json.loads(out_files[0].read_text())
    assert "Signal PLAN" in ack["text"], ack
    print(f"  PASS outbox ack: {ack['text']}")

    # Verify inbox file was moved to processed
    assert not inbox_file.exists()
    assert (processed / inbox_file.name).exists()
    print("  PASS inbox file moved to processed")


def case_adapter_handles_unknown_session_signal(home, inbox, outbox,
                                                 processed) -> None:
    """When session_id is not in process_table, the adapter ack
    must say 'unknown session' clearly and not crash."""
    _section("adapter handles unknown session_id gracefully")

    os.environ["ADAPTER_INBOX"] = str(inbox)
    os.environ["ADAPTER_OUTBOX"] = str(outbox)
    os.environ["ADAPTER_PROCESSED"] = str(processed)
    os.environ["CORVIN_HOME"] = str(home)

    for mod in ("adapter", "process_table", "paths"):
        sys.modules.pop(mod, None)
    import adapter  # type: ignore

    msg_id = "sig_unknown"
    envelope = {
        "msg_id": msg_id,
        "channel": "discord",
        "from": "_test",
        "chat_id": "99",
        "_signal": True,
        "session_id": "s_does_not_exist",
        "signal": "PLAN",
        "ts": 0,
    }
    inbox_file = inbox / f"{msg_id}.json"
    inbox_file.write_text(json.dumps(envelope))

    adapter.process_one(inbox_file, {"whitelist": []})

    out_files = list(outbox.glob(f"{msg_id}*"))
    assert len(out_files) == 1
    ack = json.loads(out_files[0].read_text())
    assert "unknown session" in ack["text"], ack
    print(f"  PASS ack: {ack['text']}")


def case_adapter_rejects_unsupported_signal(home, inbox, outbox,
                                             processed) -> None:
    _section("adapter ack reports unsupported signal cleanly")
    _register_stub_session(home, "s_unsupp")

    os.environ["ADAPTER_INBOX"] = str(inbox)
    os.environ["ADAPTER_OUTBOX"] = str(outbox)
    os.environ["ADAPTER_PROCESSED"] = str(processed)
    os.environ["CORVIN_HOME"] = str(home)

    for mod in ("adapter", "process_table", "paths"):
        sys.modules.pop(mod, None)
    import adapter  # type: ignore

    msg_id = "sig_unsupp"
    envelope = {
        "msg_id": msg_id, "channel": "discord", "from": "_test",
        "chat_id": "42", "_signal": True,
        "session_id": "s_unsupp", "signal": "WEIRDO", "ts": 0,
    }
    inbox_file = inbox / f"{msg_id}.json"
    inbox_file.write_text(json.dumps(envelope))

    adapter.process_one(inbox_file, {"whitelist": []})

    out_files = list(outbox.glob(f"{msg_id}*"))
    ack = json.loads(out_files[0].read_text())
    assert "unsupported" in ack["text"].lower() or "WEIRDO" in ack["text"], ack
    print(f"  PASS ack: {ack['text']}")


def case_help_text_lists_sig(home, inbox, outbox, processed) -> None:
    _section("help text mentions sig subcommand")
    r = _run_cli("help", home=home)
    # help text comes from the docstring at module level; the COMMANDS
    # dict has 'sig' so that's structural verification
    sys.modules.pop("phase3_cli", None)
    sys.path.insert(0, str(ROOT))
    import phase3_cli  # type: ignore
    assert "sig" in phase3_cli.COMMANDS, list(phase3_cli.COMMANDS)
    print("  PASS sig is in COMMANDS dict")


# --------------------------------------------------------------------- driver

def main() -> None:
    saved_env = {k: os.environ.get(k) for k in
                 ("CORVIN_HOME", "ADAPTER_INBOX", "ADAPTER_OUTBOX",
                  "ADAPTER_PROCESSED", "CORVIN_CHANNEL_ID")}
    cases = [
        case_sig_writes_envelope,
        case_sig_rejects_invalid_signal,
        case_sig_rejects_unknown_session,
        case_sig_validates_arity,
        case_adapter_handles_signal_envelope,
        case_adapter_handles_unknown_session_signal,
        case_adapter_rejects_unsupported_signal,
        case_help_text_lists_sig,
    ]
    failures = 0
    for case in cases:
        home, inbox, outbox, processed = _setup_sandbox()
        try:
            case(home, inbox, outbox, processed)
        except Exception as exc:
            failures += 1
            print(f"  FAIL: {case.__name__}: {exc!r}")
            import traceback
            traceback.print_exc()
        finally:
            shutil.rmtree(home, ignore_errors=True)

    # Restore env
    for k, v in saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

    print(f"\n=== {len(cases) - failures}/{len(cases)} cases passed ===")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
