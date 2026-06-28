"""Local Whisper provider via ``faster-whisper``.

Lazy-imports ``faster_whisper.WhisperModel``. If the package is
missing, ``is_available()`` returns False and the resolver falls
through. No GPU is required — CPU works for short voice notes, but
GPU is recommended for >30 s audio.

Model selection: ``CORVIN_STT_LOCAL_MODEL`` env (default ``base``).
Other options: ``tiny`` / ``small`` / ``medium`` / ``large-v3``.
Model files cache under ``~/.cache/huggingface/`` (faster-whisper
default; honoured here).

Useful when:
  * the operator runs in an air-gapped environment;
  * a tenant's data-residency policy forbids OpenAI;
  * the OPENAI_API_KEY isn't available and the bridge must still work.
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


_DEFAULT_MODEL = "base"
_DEFAULT_TIMEOUT_S = 120.0  # Local CPU runs are slower than OpenAI.


# Module-level singleton — model loads take ~5 s on first call.
_loaded_model: tuple[str, object] | None = None  # (size_name, WhisperModel)


class LocalWhisperProvider:
    """faster-whisper-backed local transcription."""

    name = "local"

    def is_available(self) -> bool:
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return False
        return True

    def _load_model(self):
        global _loaded_model
        size = os.environ.get("CORVIN_STT_LOCAL_MODEL", _DEFAULT_MODEL)
        if _loaded_model is not None and _loaded_model[0] == size:
            return _loaded_model[1]
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise STTProviderUnavailable(
                "faster-whisper not installed (pip install faster-whisper)"
            ) from exc
        # device + compute_type defaults work on CPU + GPU; faster-whisper
        # auto-selects. Operators with specific hardware override via
        # env. We don't expose those here to keep the surface tight.
        try:
            model = WhisperModel(size)
        except Exception as exc:  # noqa: BLE001
            raise STTProviderUnavailable(
                f"faster-whisper model {size!r} could not be loaded: {exc}"
            ) from exc
        _loaded_model = (size, model)
        return model

    def transcribe(
        self,
        audio_path: Path,
        *,
        lang: str | None = None,
        timeout_s: float | None = None,
    ) -> TranscriptResult:
        budget = timeout_s if timeout_s is not None else _DEFAULT_TIMEOUT_S
        model = self._load_model()

        t0 = time.monotonic()
        try:
            kwargs: dict = {}
            if lang and lang != "auto":
                kwargs["language"] = lang
            segments, info = model.transcribe(str(audio_path), **kwargs)
            # Eagerly materialise segments — they're a generator and the
            # transcription only happens when we iterate.
            pieces: list[str] = []
            for seg in segments:
                if time.monotonic() - t0 > budget:
                    raise STTTimeout(
                        f"local whisper exceeded budget {budget}s"
                    )
                pieces.append(seg.text)
        except STTTimeout:
            raise
        except FileNotFoundError as exc:
            raise STTTranscriptionFailed(
                f"audio file not found: {audio_path}"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise STTTranscriptionFailed(
                f"local whisper failed: {exc}"
            ) from exc

        text = "".join(pieces).strip()
        detected_lang = getattr(info, "language", None) or lang or None
        duration = getattr(info, "duration", None)
        return TranscriptResult(
            text=text,
            provider=self.name,
            lang=detected_lang,
            duration_s=float(duration) if duration is not None else None,
        )


assert isinstance(LocalWhisperProvider(), STTProvider)
