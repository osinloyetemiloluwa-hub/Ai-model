"""OpenAI audio transcription provider.

Default model: ``gpt-4o-mini-transcribe`` (~$0.003/min as of 2026-05),
which is 50 % cheaper than ``whisper-1`` and scores higher on German and
mixed-language voice notes. Override via ``CORVIN_STT_OPENAI_MODEL`` to
pin a different model (e.g. ``whisper-1``, ``gpt-4o-transcribe``).

Uses ``openai.audio.transcriptions.create``. Network required; data is
sent to OpenAI servers. Operators with EU-residency requirements should
disable this and pin ``local`` instead (``CORVIN_STT_PROVIDER=local``).
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from .base import (
    STTProvider,
    STTProviderUnavailable,
    STTTimeout,
    STTTranscriptionFailed,
    TranscriptResult,
)


_DEFAULT_TIMEOUT_S = 60.0
_DEFAULT_MODEL = "gpt-4o-mini-transcribe"


def _resolve_api_key() -> str | None:
    """Return the OpenAI API key, checking STT-specific var first.

    Resolution order:
      1. CORVIN_STT_OPENAI_KEY  — dedicated STT key (preferred; not counted
         as an engine credential by the console engines page)
      2. OPENAI_API_KEY         — general OpenAI key (legacy fallback)
    """
    return (
        os.environ.get("CORVIN_STT_OPENAI_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or None
    )


class OpenAIWhisperProvider:
    """OpenAI audio transcription via the ``openai`` Python SDK."""

    name = "openai"

    def is_available(self) -> bool:
        if not _resolve_api_key():
            return False
        try:
            import openai  # noqa: F401
        except ImportError:
            return False
        return True

    def transcribe(
        self,
        audio_path: Path,
        *,
        lang: str | None = None,
        timeout_s: float | None = None,
    ) -> TranscriptResult:
        api_key = _resolve_api_key()
        if not api_key:
            raise STTProviderUnavailable("no OpenAI API key set (CORVIN_STT_OPENAI_KEY or OPENAI_API_KEY)")
        try:
            from openai import OpenAI
            from openai import APIError, APITimeoutError
        except ImportError as exc:
            raise STTProviderUnavailable(
                "openai package not installed (pip install openai)"
            ) from exc

        budget = timeout_s if timeout_s is not None else _DEFAULT_TIMEOUT_S
        # The OpenAI SDK accepts a per-request timeout; honour it.
        client = OpenAI(api_key=api_key, timeout=budget)
        model = os.environ.get("CORVIN_STT_OPENAI_MODEL", _DEFAULT_MODEL)
        kwargs: dict = {"model": model}
        if lang and lang != "auto":
            kwargs["language"] = lang

        t0 = time.monotonic()
        try:
            with open(audio_path, "rb") as f:
                resp = client.audio.transcriptions.create(file=f, **kwargs)
        except APITimeoutError as exc:
            raise STTTimeout(
                f"openai whisper timeout after {time.monotonic() - t0:.1f}s"
            ) from exc
        except APIError as exc:
            raise STTTranscriptionFailed(
                f"openai whisper API error: {exc}"
            ) from exc
        except OSError as exc:
            raise STTTranscriptionFailed(
                f"audio file unreadable: {exc}"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            # Catch-all for SDK-side surprises (auth, network, etc.).
            # The SDK doesn't expose a single base class consistently
            # across versions, so we accept the breadth here.
            raise STTTranscriptionFailed(
                f"openai whisper failed: {exc}"
            ) from exc

        text = (getattr(resp, "text", "") or "").strip()
        detected_lang = getattr(resp, "language", None) or lang or None
        return TranscriptResult(
            text=text,
            provider=self.name,
            lang=detected_lang,
            duration_s=None,  # OpenAI doesn't return audio duration
        )


# Type-check at import time so a stale Protocol implementation surfaces
# loudly rather than silently dropping out of the resolver chain.
assert isinstance(OpenAIWhisperProvider(), STTProvider)
