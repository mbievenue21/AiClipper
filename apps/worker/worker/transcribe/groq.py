"""Groq backend — hosted Whisper-large-v3 via the Groq audio API.

Set ``TRANSCRIBE_BACKEND=groq`` and ``GROQ_API_KEY=...`` to use this. Useful if
you don't have a GPU available locally. Output is normalised to the same
TranscriptResult shape that the local backend returns.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from ..config import get_settings
from . import ProgressCb, TranscriptResult, TranscriptSegmentOut

log = structlog.get_logger(__name__)

GROQ_MODEL = "whisper-large-v3"


def transcribe_groq(
    audio_path: Path,
    *,
    duration_seconds: float | None,  # noqa: ARG001 — kept for API parity
    progress: ProgressCb,
) -> TranscriptResult:
    settings = get_settings()
    if not settings.groq_api_key:
        raise RuntimeError(
            "TRANSCRIBE_BACKEND=groq but GROQ_API_KEY is unset. "
            "Add it to .env or switch to TRANSCRIBE_BACKEND=local."
        )

    try:
        from groq import Groq
    except ImportError as exc:
        raise RuntimeError(
            "groq client is not installed. Run: pnpm --filter worker run setup:transcribe-groq"
        ) from exc

    progress(0.10, "uploading audio to Groq")
    client = Groq(api_key=settings.groq_api_key)

    with audio_path.open("rb") as fh:
        response = client.audio.transcriptions.create(
            file=(audio_path.name, fh.read()),
            model=GROQ_MODEL,
            response_format="verbose_json",
            timestamp_granularities=["segment", "word"],
        )

    progress(0.85, "parsing Groq response")
    data: dict[str, Any] = response.model_dump() if hasattr(response, "model_dump") else dict(response)

    raw_segments: list[dict[str, Any]] = data.get("segments") or []
    raw_words: list[dict[str, Any]] = data.get("words") or []

    # Groq returns word timings as a flat list. Bucket them into segments by
    # comparing each word's timestamp to the segment range.
    out_segments: list[TranscriptSegmentOut] = []
    word_idx = 0
    for seg in raw_segments:
        seg_start = float(seg.get("start", 0.0))
        seg_end = float(seg.get("end", seg_start))
        seg_words: list[dict[str, Any]] = []
        while word_idx < len(raw_words):
            w = raw_words[word_idx]
            w_end = float(w.get("end", seg_end))
            if w_end > seg_end + 0.01:
                break
            seg_words.append(
                {
                    "word": (w.get("word") or "").strip(),
                    "start": float(w.get("start", seg_start)),
                    "end": float(w.get("end", seg_end)),
                    # Groq does not return per-word probabilities; surface 1.0.
                    "confidence": 1.0,
                }
            )
            word_idx += 1

        out_segments.append(
            TranscriptSegmentOut(
                start_seconds=seg_start,
                end_seconds=seg_end,
                text=(seg.get("text") or "").strip(),
                words=seg_words,
            )
        )

    return TranscriptResult(
        language=data.get("language"),
        model_name=f"groq:{GROQ_MODEL}",
        full_text=(data.get("text") or "").strip(),
        segments=out_segments,
    )
