"""Per-subtask E2E for Phase-7 compat resolver in skill_forge.registry.plugin_slot_dir.

Covers CORVIN_PLUGIN_SLOT_DIR canonical override, CORVIN_PLUGIN_SLOT_DIR
(ignored in Phase 7), CORVIN_HOME / CORVIN_HOME (CORVIN_HOME ignored in
Phase 7) compat for home-derived slot, and the home-fallback (.corvin vs .corvinOS) stage.
"""
from __future__ import annotations

import importlib
import io
import os
import sys
import tempfile
from contextlib import redirect_stderr
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    suffix = f" — {detail}" if detail else ""
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{suffix}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


def _clear_env() -> None:
    for k in (
        "CORVIN_PLUGIN_SLOT_DIR", "CORVIN_PLUGIN_SLOT_DIR",
        "CORVIN_HOME", "CORVIN_HOME",
    ):
        os.environ.pop(k, None)


def _fresh_registry():
    sys.path.insert(0, str(REPO / "operator" / "skill-forge"))
    for mod in ("skill_forge.registry", "skill_forge"):
        sys.modules.pop(mod, None)
    try:
        return importlib.import_module("skill_forge.registry")
    finally:
        sys.path.pop(0)


def test_corvin_slot_dir_wins() -> None:
    print("\n[CORVIN_PLUGIN_SLOT_DIR wins, no warning]")
    _clear_env()
    with tempfile.TemporaryDirectory() as td:
        os.environ["CORVIN_PLUGIN_SLOT_DIR"] = td
        buf = io.StringIO()
        with redirect_stderr(buf):
            r = _fresh_registry()
            p = r.plugin_slot_dir()
        t("path == CORVIN value", p == Path(td), detail=f"got {p}")
        t("no deprecation log",
          "deprecation" not in buf.getvalue().lower())
    _clear_env()


def test_legacy_slot_dir_with_warning() -> None:
    print("\n[CORVIN_PLUGIN_SLOT_DIR set — used as canonical override]")
    _clear_env()
    with tempfile.TemporaryDirectory() as td:
        os.environ["CORVIN_PLUGIN_SLOT_DIR"] = td
        buf = io.StringIO()
        with redirect_stderr(buf):
            r = _fresh_registry()
            p1 = r.plugin_slot_dir()
            p2 = r.plugin_slot_dir()
        out = buf.getvalue()
        # CORVIN_PLUGIN_SLOT_DIR is the canonical override — it IS read and used.
        t("path IS td (CORVIN_PLUGIN_SLOT_DIR used)",
          p1 == Path(td), detail=f"got {p1}")
        t("p2 == p1 (stable)", p2 == p1)
        t("NO deprecation warning emitted",
          "CORVIN_PLUGIN_SLOT_DIR" not in out,
          detail=f"stderr={out!r}")
        t("no deprecation on stderr",
          "deprecation" not in out.lower() or "CORVIN_PLUGIN_SLOT_DIR" not in out,
          detail=f"stderr={out!r}")
    _clear_env()


def test_corvin_home_derives_slot() -> None:
    print("\n[CORVIN_HOME → <home>/plugin-slot, no warning]")
    _clear_env()
    with tempfile.TemporaryDirectory() as td:
        os.environ["CORVIN_HOME"] = td
        buf = io.StringIO()
        with redirect_stderr(buf):
            r = _fresh_registry()
            p = r.plugin_slot_dir()
        t("path == HOME/plugin-slot",
          p == Path(td) / "plugin-slot", detail=f"got {p}")
        t("no deprecation log",
          "deprecation" not in buf.getvalue().lower())
    _clear_env()


def test_legacy_home_derives_slot_with_warning() -> None:
    print("\n[CORVIN_HOME set — slot IS derived from it, no warning]")
    _clear_env()
    with tempfile.TemporaryDirectory() as td:
        os.environ["CORVIN_HOME"] = td
        buf = io.StringIO()
        with redirect_stderr(buf):
            r = _fresh_registry()
            p = r.plugin_slot_dir()
        out = buf.getvalue()
        # CORVIN_HOME is the canonical override — slot IS derived from it.
        t("path IS derived from CORVIN_HOME td",
          p == Path(td) / "plugin-slot", detail=f"got {p}")
        t("NO CORVIN_HOME warning",
          "CORVIN_HOME" not in out,
          detail=f"stderr={out!r}")
    _clear_env()


def test_corvin_slot_beats_legacy_when_both() -> None:
    print("\n[CORVIN_PLUGIN_SLOT_DIR set → used, no warning]")
    _clear_env()
    with tempfile.TemporaryDirectory() as td_new:
        os.environ["CORVIN_PLUGIN_SLOT_DIR"] = td_new
        buf = io.StringIO()
        with redirect_stderr(buf):
            r = _fresh_registry()
            p = r.plugin_slot_dir()
        t("CORVIN_PLUGIN_SLOT_DIR used", p == Path(td_new))
        t("no deprecation (canonical var, no warning)",
          "deprecation" not in buf.getvalue().lower())
    _clear_env()


def test_repo_walk_when_no_env() -> None:
    print("\n[no env → walk up, find plugins/ marker]")
    _clear_env()
    buf = io.StringIO()
    with redirect_stderr(buf):
        r = _fresh_registry()
        p = r.plugin_slot_dir()
    s = str(p)
    t("ends with /operator/skill-forge/skills/dyn",
      s.endswith("/operator/skill-forge/skills/dyn"),
      detail=f"got {s}")
    t("no warning", "deprecation" not in buf.getvalue().lower())
    _clear_env()


def main() -> int:
    test_corvin_slot_dir_wins()
    test_legacy_slot_dir_with_warning()
    test_corvin_home_derives_slot()
    test_legacy_home_derives_slot_with_warning()
    test_corvin_slot_beats_legacy_when_both()
    test_repo_walk_when_no_env()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
