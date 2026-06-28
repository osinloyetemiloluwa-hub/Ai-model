#!/usr/bin/env python3
"""profile_cli.py — CLI in front of profile.py for the in-chat `/profile`
commands. Sub-commands:

    show
    get <key>
    set <key>=<value>
    rm <key>            (alias: unset)
    reset
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHARED = HERE.parent.parent / "bridges" / "shared"
sys.path.insert(0, str(SHARED))

import profile as prof  # noqa: E402


def cmd_show(_args: list[str]) -> int:
    print(prof.humanize())
    return 0


def cmd_get(args: list[str]) -> int:
    if not args:
        print("Usage: profile_cli.py get <key>")
        return 2
    v = prof.get(args[0])
    if v is None:
        print(f"(no value set for {args[0]})")
    else:
        print(f"{args[0]}: {v}")
    return 0


def cmd_set(args: list[str]) -> int:
    if not args:
        print("Usage: profile_cli.py set <key>=<value>")
        return 2
    body = " ".join(args)
    if "=" not in body:
        print("Error: expected `key=value`. Example: set name=Silvio")
        return 2
    key, raw = body.split("=", 1)
    key = key.strip()
    if not key:
        print("Error: empty key.")
        return 2
    val = prof.parse_value(raw)
    prof.set_value(key, val)
    if val is None:
        print(f"Removed {key}.")
    else:
        print(f"Set {key} = {val}")
    return 0


def cmd_rm(args: list[str]) -> int:
    if not args:
        print("Usage: profile_cli.py rm <key>")
        return 2
    prof.set_value(args[0], None)
    print(f"Removed {args[0]}.")
    return 0


def cmd_reset(_args: list[str]) -> int:
    prof.reset()
    print("Profile cleared.")
    return 0


_DISPATCH = {
    "show": cmd_show,
    "get": cmd_get,
    "set": cmd_set,
    "rm": cmd_rm,
    "unset": cmd_rm,
    "reset": cmd_reset,
}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        # Bare invocation: behave like `show`.
        return cmd_show([])
    sub = argv[1].lower()
    rest = argv[2:]
    fn = _DISPATCH.get(sub)
    if fn is None:
        print(f"Unknown sub-command: {sub}")
        print("Try: show | get <key> | set <key>=<value> | rm <key> | reset")
        return 2
    return fn(rest)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
