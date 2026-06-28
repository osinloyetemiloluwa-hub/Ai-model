#!/usr/bin/env python3
"""test_adapter_voice_audience.py — Layer-12 audience block in the bridge
voice-note path.

Pro CLAUDE.md (`feedback_per_subtask_e2e`): until 2026-05-08 the bridge's
build_voice_summary spawned summarize.py *without* `--audience`, so the
listener-profile (incl. voice_audience_learning=3 LERN-ZUGABE) was dead
code for every Discord/WhatsApp/Telegram/Slack chat. Stop_hook.sh is
the only place that passed --audience, but the bridge always exports
VOICE_HOOK_RECURSION=1, so the stop_hook short-circuits and never
reaches summarize.py for bridge replies.

Three subtests, all per-subtask E2E with a real subprocess pipeline:

  1. With voice_audience_learning=3 set in the profile, build_voice_summary
     calls summarize.py with `--audience <block>` AND the block contains
     the LERN-ZUGABE / LEARNING ANNEX clause.
  2. Without any audience fields, --audience is omitted entirely
     (backward-compat: byte-identical argv to the pre-fix path).
  3. When the profile module is unavailable, build_voice_summary still
     works (graceful no-op fallback, mirrors skill_inject pattern).

The summarize.py target is replaced by a fake CLI script that dumps its
argv to a sidecar file — no LLM, no API hit, deterministic.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _install_fake_summarizer(tmp: Path) -> tuple[Path, Path]:
    """Replace summarize.py and strip_for_tts.py with deterministic fakes.

    The fakes write their argv (as JSON) to a sidecar file the test reads
    back, then echo a stable string on stdout. That isolates the test
    from the real summarize.py LLM call.

    Returns: (scripts_dir, argv_dump_path).
    """
    scripts_dir = tmp / "scripts"
    scripts_dir.mkdir()
    argv_dump = tmp / "summarizer_argv.json"

    fake_summarize = scripts_dir / "summarize.py"
    fake_summarize.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys, os\n"
        "argv = sys.argv[1:]\n"
        # Dump ONLY the MAIN summarize call's argv. The LERN-ZUGABE / Metapher
        # deterministic backfills re-invoke this same script with --appendix-mode
        # / --metapher-mode AFTER the main call; without this guard those later
        # calls would overwrite the dump and the test would assert on the wrong
        # invocation.
        "if '--appendix-mode' not in argv and '--metapher-mode' not in argv:\n"
        f"    open({json.dumps(str(argv_dump))}, 'w').write(json.dumps(argv))\n"
        "print('FAKE_SUMMARY_OUTPUT')\n"
    )
    fake_summarize.chmod(0o755)

    fake_stripper = scripts_dir / "strip_for_tts.py"
    fake_stripper.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.stdout.write(sys.stdin.read())\n"
    )
    fake_stripper.chmod(0o755)

    return scripts_dir, argv_dump


def _fresh_adapter_with_scripts_dir(scripts_dir: Path):
    """Re-import adapter with SCRIPTS_DIR redirected at the fake scripts."""
    # Caller is expected to have already cleared sys.modules for both
    # 'adapter' and 'profile' so PROFILE_FILE re-resolves against any
    # XDG_CONFIG_HOME override.
    import adapter  # type: ignore
    # Patch SCRIPTS_DIR to point at the fakes. We do this on the imported
    # module rather than via env so build_voice_summary picks it up
    # regardless of how SCRIPTS_DIR was originally derived.
    adapter.SCRIPTS_DIR = scripts_dir
    return adapter


def test_learning_3_passes_audience_arg() -> None:
    _section("learning=3 → --audience appears with LERN-ZUGABE clause")
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        # Per-test profile dir to avoid polluting the user's real
        # ~/.config/corvin-voice/profile.json.
        profile_dir = td_path / "voice-config"
        profile_dir.mkdir()
        os.environ["XDG_CONFIG_HOME"] = str(profile_dir)
        # Drop cached profile + adapter modules so PROFILE_FILE re-resolves
        # against the new XDG_CONFIG_HOME and the in-process load() cache
        # starts empty.
        for m in ("profile", "adapter"):
            sys.modules.pop(m, None)

        scripts_dir, argv_dump = _install_fake_summarizer(td_path)
        adapter = _fresh_adapter_with_scripts_dir(scripts_dir)
        # Ensure adapter's _voice_profile actually re-resolved against
        # the new VOICE_CONFIG_DIR.
        assert adapter._voice_profile is not None, (
            "profile module failed to import — fix the optional-import path"
        )
        adapter._voice_profile.set_value("voice_audience_learning", 3)

        # Build a long-enough input that triggers the summarize path
        # (build_voice_summary returns text directly when len <= max_chars).
        long_text = "Das ist ein langer Test. " * 80  # ~2000 chars
        result = adapter.build_voice_summary(long_text, max_chars=400)
        assert result, "build_voice_summary returned empty"

        argv = json.loads(argv_dump.read_text())
        assert "--audience" in argv, (
            f"--audience missing from argv: {argv}"
        )
        idx = argv.index("--audience")
        block = argv[idx + 1]
        assert "LERN-ZUGABE" in block, (
            f"audience block does not contain LERN-ZUGABE: {block!r}"
        )
        assert "Lern-Modus 3/3" in block, (
            f"learning level not 3/3 in rendered block: {block!r}"
        )
        # Sanity: the fake summarizer also got --lang de --max-chars 400
        assert argv[:4] == ["--lang", "de", "--max-chars", "400"], (
            f"unexpected argv prefix: {argv[:4]}"
        )
        print(f"  OK — argv passed --audience ({len(block)} chars)")


def test_no_audience_field_omits_arg() -> None:
    _section("empty profile → --audience NOT passed (backward-compat)")
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        profile_dir = td_path / "voice-config"
        profile_dir.mkdir()
        os.environ["VOICE_CONFIG_DIR"] = str(profile_dir)
        for m in ("profile",):
            sys.modules.pop(m, None)

        scripts_dir, argv_dump = _install_fake_summarizer(td_path)
        adapter = _fresh_adapter_with_scripts_dir(scripts_dir)
        # Don't set any audience fields — load() returns {} → for_tts_audience
        # returns "".

        result = adapter.build_voice_summary("Das ist ein langer Test. " * 80,
                                              max_chars=400)
        assert result, "build_voice_summary returned empty"

        argv = json.loads(argv_dump.read_text())
        assert "--audience" not in argv, (
            f"--audience should NOT be present when profile is empty: {argv}"
        )
        print("  OK — no --audience when profile is empty")


def test_profile_module_missing_graceful() -> None:
    _section("_voice_profile=None → still summarizes, no crash")
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        for m in ("profile", "adapter"):
            sys.modules.pop(m, None)
        scripts_dir, argv_dump = _install_fake_summarizer(td_path)
        adapter = _fresh_adapter_with_scripts_dir(scripts_dir)
        # Force the optional-import to None and verify graceful fallback.
        adapter._voice_profile = None

        result = adapter.build_voice_summary("Das ist ein langer Test. " * 80,
                                              max_chars=400)
        assert result, "build_voice_summary should still work without profile"

        argv = json.loads(argv_dump.read_text())
        assert "--audience" not in argv, (
            f"--audience must not appear when _voice_profile is None: {argv}"
        )
        print("  OK — graceful fallback when profile module unavailable")


def main() -> int:
    test_learning_3_passes_audience_arg()
    test_no_audience_field_omits_arg()
    test_profile_module_missing_graceful()
    print("\nAll voice-audience adapter tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
