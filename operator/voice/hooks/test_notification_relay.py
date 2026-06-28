#!/usr/bin/env python3
"""Test für notification_relay.py.

Prüft alle Pfade ohne Claude Code zu starten:
  1. Kein relay.json → No-Op (exit 0, kein Outbox-Write)
  2. enabled=false → No-Op
  3. Notification-Event mit message → Outbox-Envelope
  4. SessionStart startup → Outbox-Envelope mit cwd
  5. SessionStart resume → kein Forward
  6. Event nicht in events-Filter → kein Forward

Run:
    python3 operator/voice/hooks/test_notification_relay.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT     = Path(__file__).resolve().parent
RELAY    = ROOT / "notification_relay.py"
PLUGIN   = ROOT.parent
OUTBOX   = PLUGIN / "bridges" / "shared" / "outbox"


def run_hook(payload: dict, config: dict | None,
             config_dir: Path) -> tuple[int, list[Path]]:
    """Ruft das Skript auf, liefert (exitcode, list_of_relay_files)."""
    config_dir.mkdir(parents=True, exist_ok=True)
    relay_cfg = config_dir / "relay.json"
    if config is None:
        relay_cfg.unlink(missing_ok=True)
    else:
        relay_cfg.write_text(json.dumps(config))

    # Outbox vor dem Lauf säubern (nur unsere relay_*-Files).
    pre = {p.name for p in OUTBOX.glob("relay_*.json")}

    env = os.environ.copy()
    env["VOICE_CONFIG_DIR"] = str(config_dir)
    res = subprocess.run(
        ["python3", str(RELAY)],
        input=json.dumps(payload), text=True, capture_output=True, env=env,
    )
    new_files = [p for p in OUTBOX.glob("relay_*.json") if p.name not in pre]
    return res.returncode, new_files


def cleanup(files: list[Path]) -> None:
    for f in files:
        f.unlink(missing_ok=True)


def main() -> int:
    failures: list[str] = []
    OUTBOX.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # --- 1. Kein relay.json → No-Op ----------------------------------
        rc, files = run_hook(
            {"hook_event_name": "Notification", "message": "ignore me"},
            config=None, config_dir=tmp / "noconfig",
        )
        if rc != 0 or files:
            failures.append(f"no-config: rc={rc} files={files}")
        else:
            print("PASS: no relay.json → no forward, exit 0")
        cleanup(files)

        # --- 2. enabled=false → No-Op ------------------------------------
        rc, files = run_hook(
            {"hook_event_name": "Notification", "message": "still ignore"},
            config={"enabled": False, "channel": "telegram", "to": "1"},
            config_dir=tmp / "disabled",
        )
        if rc != 0 or files:
            failures.append(f"disabled: rc={rc} files={files}")
        else:
            print("PASS: enabled=false → no forward")
        cleanup(files)

        # --- 3. Notification mit message → Outbox-Envelope ---------------
        rc, files = run_hook(
            {"hook_event_name": "Notification",
             "message": "Tool needs approval: Bash"},
            config={"enabled": True, "channel": "telegram", "to": "999",
                    "events": ["Notification"], "prefix": "🔔"},
            config_dir=tmp / "notify",
        )
        if rc != 0 or len(files) != 1:
            failures.append(f"notification: rc={rc} files={files}")
        else:
            env = json.loads(files[0].read_text())
            if (env.get("channel") != "telegram"
                    or env.get("chat_id") != 999
                    or "Tool needs approval" not in env.get("text", "")
                    or not env["text"].startswith("🔔")):
                failures.append(f"notification envelope shape: {env}")
            else:
                print("PASS: Notification → telegram envelope with prefix + chat_id")
        cleanup(files)

        # --- 4. SessionStart startup → Forward ---------------------------
        rc, files = run_hook(
            {"hook_event_name": "SessionStart", "source": "startup",
             "cwd": "/home/foo/myproject"},
            config={"enabled": True, "channel": "discord", "to": "12345",
                    "events": ["Notification", "SessionStart"]},
            config_dir=tmp / "session",
        )
        if rc != 0 or len(files) != 1:
            failures.append(f"sessionstart: rc={rc} files={files}")
        else:
            env = json.loads(files[0].read_text())
            if "myproject" not in env.get("text", ""):
                failures.append(f"sessionstart text missing cwd: {env}")
            else:
                print("PASS: SessionStart startup → discord envelope with cwd")
        cleanup(files)

        # --- 5. SessionStart resume → kein Forward -----------------------
        rc, files = run_hook(
            {"hook_event_name": "SessionStart", "source": "resume",
             "cwd": "/home/foo/myproject"},
            config={"enabled": True, "channel": "discord", "to": "12345",
                    "events": ["Notification", "SessionStart"]},
            config_dir=tmp / "resume",
        )
        if rc != 0 or files:
            failures.append(f"resume: rc={rc} files={files}")
        else:
            print("PASS: SessionStart resume → no forward (only startup)")
        cleanup(files)

        # --- 6. Event nicht im Filter → kein Forward ---------------------
        rc, files = run_hook(
            {"hook_event_name": "Stop", "message": "should not pass"},
            config={"enabled": True, "channel": "telegram", "to": "1",
                    "events": ["Notification"]},
            config_dir=tmp / "filter",
        )
        if rc != 0 or files:
            failures.append(f"event-filter: rc={rc} files={files}")
        else:
            print("PASS: events-filter blocks Stop event")
        cleanup(files)

    if failures:
        print(f"\n{len(failures)} FAILURE(S):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nALL CHECKS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
