"""Transcription dispatcher.

Two backends are supported:

* ``local``  — faster-whisper running on the local GPU (or CPU fallback). This
  is the default and the path that Step 6 of the build plan is wired for.
* ``groq``   — Groq's hosted Whisper-large-v3 endpoint. Useful when you don't
  have a GPU handy.

Both return the same :class:`TranscriptResult` shape so the job handler
doesn't need to care which one ran.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import get_settings

# Re-exported for convenience.
ProgressCb = Callable[[float, str | None], None]


@dataclass
class TranscriptSegmentOut:
    start_seconds: float
    end_seconds: float
    text: str
    # Word timings: [{"word": str, "start": float, "end": float, "confidence": float}]
    words: list[dict[str, Any]]


@dataclass
class TranscriptResult:
    language: str | None
    model_name: str  # e.g. "faster-whisper:large-v3" or "groq:whisper-large-v3"
    full_text: str
    segments: list[TranscriptSegmentOut]


def transcribe(
    audio_path: Path,
    *,
    duration_seconds: float | None,
    progress: ProgressCb,
) -> TranscriptResult:
    """Dispatch to the configured backend (env: ``TRANSCRIBE_BACKEND``)."""
    backend = get_settings().transcribe_backend
    if backend == "local":
        from .local import transcribe_local

        return transcribe_local(audio_path, duration_seconds=duration_seconds, progress=progress)
    if backend == "groq":
        from .groq import transcribe_groq

        return transcribe_groq(audio_path, duration_seconds=duration_seconds, progress=progress)
    raise ValueError(f"Unknown TRANSCRIBE_BACKEND: {backend!r}")
