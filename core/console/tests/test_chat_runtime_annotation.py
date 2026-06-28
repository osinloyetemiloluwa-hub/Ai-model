"""Regression: the console-chat voice-annotation pipeline (LERN-ZUGABE +
METAPHER) must read the voice profile through the SAME canonical, XDG-aware
loader the console profile editor writes through — not a hardcoded
tenant_home/voice/profile.json. When XDG_CONFIG_HOME is set (interactive /
systemd-user launch), the writer lands in ~/.config/corvin-voice/profile.json
while the old hardcoded reader looked at <corvin_home>/tenants/<tid>/voice/ —
reader != writer, so Learning=3 and Metaphern=on silently did nothing.
"""
import asyncio
import types

import pytest

from corvin_console import chat_runtime as cr


def _stub_summarizer(monkeypatch):
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        mode = ("APPENDIX" if "--appendix-mode" in argv
                else "METAPHER" if "--metapher-mode" in argv else "?")
        inp = kw.get("input", "")
        return types.SimpleNamespace(stdout=f"{inp} [{mode}]", stderr="", returncode=0)

    monkeypatch.setattr(cr.subprocess, "run", fake_run)
    return calls


def _fake_profile(monkeypatch, *, chat_render=True, **values):
    # chat_render defaults to True here so the learning/metaphor tests exercise
    # the annotation path; the dedicated chat_render test flips it off.
    class _P:
        @staticmethod
        def load(force=False):
            return dict(values)

        @staticmethod
        def chat_render_enabled():
            return chat_render
    monkeypatch.setattr(cr, "_voice_profile", _P)


def test_learning_and_metaphors_on_produce_suffix(monkeypatch):
    calls = _stub_summarizer(monkeypatch)
    _fake_profile(monkeypatch, voice_audience_learning=3, voice_audience_metaphors="on")
    suffix = asyncio.run(cr._compute_web_annotation_suffix("The cache stores results.", "_default"))
    invoked = [a[-1] for a in calls]
    assert "--appendix-mode" in invoked and "--metapher-mode" in invoked
    assert suffix, "suffix must be non-empty when learning>0 / metaphors=on"


def test_both_off_returns_empty_and_runs_no_subprocess(monkeypatch):
    calls = _stub_summarizer(monkeypatch)
    _fake_profile(monkeypatch, voice_audience_learning=0, voice_audience_metaphors="off")
    suffix = asyncio.run(cr._compute_web_annotation_suffix("anything", "_default"))
    assert suffix == ""
    assert calls == [], "no annotation subprocess when both features are off"


def test_reads_canonical_loader_not_tenant_home(monkeypatch):
    """Guard against re-introducing the reader!=writer split: the value comes
    from _voice_profile.load(), independent of any tenant_home file."""
    _stub_summarizer(monkeypatch)
    seen = {}

    class _P:
        @staticmethod
        def load(force=False):
            seen["force"] = force
            return {"voice_audience_learning": 3}

        @staticmethod
        def chat_render_enabled():
            return True
    monkeypatch.setattr(cr, "_voice_profile", _P)
    suffix = asyncio.run(cr._compute_web_annotation_suffix("x.", "any-tenant"))
    assert seen.get("force") is True, "must force-refresh so just-saved toggles apply"
    assert suffix


def test_chat_render_off_suppresses_suffix_even_with_features_on(monkeypatch):
    """Voice-only: with chat_render=off the annex must NOT enter the chat text,
    even when learning/metaphors are on (it still rides the TTS/voice path)."""
    calls = _stub_summarizer(monkeypatch)
    _fake_profile(monkeypatch, chat_render=False,
                  voice_audience_learning=3, voice_audience_metaphors="on")
    suffix = asyncio.run(cr._compute_web_annotation_suffix("The cache stores results.", "_default"))
    assert suffix == "", "chat_render=off must keep the annex out of the chat text"
    assert calls == [], "no annotation subprocess when chat_render is off"


def test_missing_profile_module_is_safe(monkeypatch):
    monkeypatch.setattr(cr, "_voice_profile", None)
    suffix = asyncio.run(cr._compute_web_annotation_suffix("x.", "_default"))
    assert suffix == ""
