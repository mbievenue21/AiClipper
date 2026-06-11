"""Optional WhisperX forced alignment for tighter word timings."""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from ..config import get_settings

log = structlog.get_logger(__name__)


def whisperx_enabled() -> bool:
    return get_settings().whisperx_enabled


def align_transcript_segments(
    audio_path: Path,
    segments: list[dict],
) -> list[dict] | None:
    """Return segments with refined word timings, or None if unavailable."""
    if not whisperx_enabled() or not audio_path.exists():
        return None
    try:
        import whisperx
    except ImportError:
        log.warning("whisperx_not_installed")
        return None

    try:
        device = "cuda" if get_settings().whisper_device == "cuda" else "cpu"
        model = whisperx.load_model(
            get_settings().whisper_model,
            device=device,
            compute_type=get_settings().whisper_compute_type,
        )
        audio = whisperx.load_audio(str(audio_path))
        result = model.transcribe(audio, batch_size=8)
        model_a, metadata = whisperx.load_align_model(
            language_code=result.get("language", "en"),
            device=device,
        )
        aligned = whisperx.align(
            result["segments"],
            model_a,
            metadata,
            audio,
            device,
            return_char_alignments=False,
        )
        out: list[dict] = []
        for seg in aligned.get("segments", []):
            out.append(
                {
                    "start": float(seg.get("start", 0)),
                    "end": float(seg.get("end", 0)),
                    "text": str(seg.get("text", "")).strip(),
                    "words": seg.get("words", []),
                }
            )
        return out or None
    except Exception as exc:
        log.warning("whisperx_align_failed", error=str(exc))
        return None
