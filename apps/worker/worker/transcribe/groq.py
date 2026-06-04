"""Groq backend — hosted Whisper-large-v3 via the Groq audio API.

Set ``TRANSCRIBE_BACKEND=groq`` and ``GROQ_API_KEY=...`` to use this. Useful if
you don't have a GPU available locally. Output is normalised to the same
TranscriptResult shape that the local backend returns.

Size handling
-------------
Groq caps audio uploads at 25 MB per request on ALL tiers — paid plans only
raise rate limits, not the file-size ceiling. We deal with that two ways:

1. Compress before upload. Ingest writes 16 kHz mono PCM WAV which is
   ~1.9 MB/min. Transcoding to Opus at 32 kbps drops that to ~240 KB/min —
   a 14-minute clip goes from 25 MB to ~3.4 MB. Anything under ~95 minutes
   fits in a single request comfortably.
2. Chunk by time when the compressed file is still too large (e.g. a 2-hour
   VOD). We split into ~25-minute Opus chunks via ffmpeg's segment muxer,
   transcribe each, and stitch the results back together with the cumulative
   start-time offset added to every segment/word timestamp.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import structlog

from ..config import get_settings

# We import the sync helpers from ffmpeg_util because this whole module runs
# inside `asyncio.to_thread` (see jobs/transcribe.py) — there's no event loop
# available for the `async def` wrappers.
from ..media.ffmpeg_util import _compress_to_opus_sync, _split_audio_sync
from . import ProgressCb, TranscriptResult, TranscriptSegmentOut

log = structlog.get_logger(__name__)

GROQ_MODEL = "whisper-large-v3"

# Groq's documented per-request limit is 25 MB. We aim well below to leave
# headroom for the multipart-form envelope.
_GROQ_MAX_BYTES = 23 * 1024 * 1024  # 23 MB

# Compressed-audio bitrate. Speech transcription quality is essentially flat
# above 24 kbps for Whisper; 32 kbps gives us a safety margin without bloat.
_OPUS_KBPS = 32

# Chunk length when the compressed file is STILL too big (multi-hour VODs).
# 25 min at 32 kbps Opus is ~6–7 MB, comfortably under 23 MB.
_CHUNK_SECONDS = 25 * 60


def _human_size(n_bytes: int) -> str:
    if n_bytes < 1024:
        return f"{n_bytes} B"
    units = ["KB", "MB", "GB"]
    val = float(n_bytes) / 1024.0
    for u in units:
        if val < 1024.0 or u == units[-1]:
            return f"{val:.1f} {u}"
        val /= 1024.0
    return f"{n_bytes} B"


def _post_one(client: Any, audio_path: Path) -> dict[str, Any]:
    """Send a single file to Groq and return the parsed JSON dict."""
    with audio_path.open("rb") as fh:
        response = client.audio.transcriptions.create(
            file=(audio_path.name, fh.read()),
            model=GROQ_MODEL,
            response_format="verbose_json",
            timestamp_granularities=["segment", "word"],
        )
    return response.model_dump() if hasattr(response, "model_dump") else dict(response)


def _normalize_segments(data: dict[str, Any], *, time_offset: float) -> list[TranscriptSegmentOut]:
    """Convert a Groq verbose_json response into our normalised segment list.

    `time_offset` lets us shift timestamps for chunked uploads — every segment
    and word gets the offset added so timing stays correct across the merged
    transcript.
    """
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
                    "start": float(w.get("start", seg_start)) + time_offset,
                    "end": float(w.get("end", seg_end)) + time_offset,
                    "confidence": 1.0,  # Groq doesn't return per-word probs
                }
            )
            word_idx += 1

        out_segments.append(
            TranscriptSegmentOut(
                start_seconds=seg_start + time_offset,
                end_seconds=seg_end + time_offset,
                text=(seg.get("text") or "").strip(),
                words=seg_words,
            )
        )
    return out_segments


def transcribe_groq(
    audio_path: Path,
    *,
    duration_seconds: float | None,
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

    client = Groq(api_key=settings.groq_api_key)

    # Work inside a temp dir so any chunked outputs get cleaned up on exit.
    with tempfile.TemporaryDirectory(prefix="groq_audio_") as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        compressed = tmpdir / "audio.ogg"

        # ----- Step 1: compress to Opus --------------------------------------
        progress(0.05, "compressing audio for upload")
        _compress_to_opus_sync(audio_path, compressed, bitrate_kbps=_OPUS_KBPS)

        original_size = audio_path.stat().st_size
        compressed_size = compressed.stat().st_size
        log.info(
            "groq_compressed",
            original_bytes=original_size,
            compressed_bytes=compressed_size,
            ratio=round(original_size / max(compressed_size, 1), 1),
        )
        progress(
            0.15,
            f"compressed {_human_size(original_size)} → {_human_size(compressed_size)}",
        )

        # ----- Step 2: single-shot if it fits --------------------------------
        if compressed_size <= _GROQ_MAX_BYTES:
            progress(0.20, f"uploading {_human_size(compressed_size)} to Groq")
            data = _post_one(client, compressed)
            progress(0.85, "parsing Groq response")
            segments = _normalize_segments(data, time_offset=0.0)
            return TranscriptResult(
                language=data.get("language"),
                model_name=f"groq:{GROQ_MODEL}",
                full_text=(data.get("text") or "").strip(),
                segments=segments,
            )

        # ----- Step 3: chunked upload for very long audio -------------------
        log.info(
            "groq_chunking",
            reason="compressed_too_large",
            compressed_bytes=compressed_size,
            limit_bytes=_GROQ_MAX_BYTES,
        )
        progress(
            0.20,
            f"audio too long for single upload ({_human_size(compressed_size)}); chunking",
        )

        chunks_dir = tmpdir / "chunks"
        chunk_paths = _split_audio_sync(
            audio_path,
            chunks_dir,
            chunk_seconds=_CHUNK_SECONDS,
            bitrate_kbps=_OPUS_KBPS,
        )
        if not chunk_paths:
            raise RuntimeError(
                "Audio chunking produced no files. Check ffmpeg's segment muxer."
            )

        log.info("groq_chunk_count", count=len(chunk_paths))

        merged_segments: list[TranscriptSegmentOut] = []
        merged_text: list[str] = []
        language: str | None = None

        # ffmpeg's segment muxer respects `-segment_time` but the actual chunk
        # boundary may be a few ms off when the input doesn't divide evenly.
        # We trust our request: chunk_index * chunk_seconds is a perfectly
        # acceptable offset because Groq returns timestamps relative to the
        # chunk anyway. (If you need bit-exact merging, switch to ffprobe per
        # chunk — overkill for our use case.)
        per_chunk_progress_band = 0.65 / len(chunk_paths)
        for idx, chunk in enumerate(chunk_paths):
            base_progress = 0.20 + per_chunk_progress_band * idx
            progress(
                base_progress,
                f"uploading chunk {idx + 1}/{len(chunk_paths)} ({_human_size(chunk.stat().st_size)})",
            )
            if chunk.stat().st_size > _GROQ_MAX_BYTES:
                # Shouldn't happen at 32 kbps with 25-min chunks, but guard.
                raise RuntimeError(
                    f"Chunk {chunk.name} is {_human_size(chunk.stat().st_size)}, "
                    f"still over Groq's {_human_size(_GROQ_MAX_BYTES)} limit. "
                    "Lower _OPUS_KBPS or shorten _CHUNK_SECONDS."
                )
            data = _post_one(client, chunk)
            offset = float(idx * _CHUNK_SECONDS)
            merged_segments.extend(_normalize_segments(data, time_offset=offset))
            chunk_text = (data.get("text") or "").strip()
            if chunk_text:
                merged_text.append(chunk_text)
            if language is None:
                language = data.get("language")

        progress(0.85, "merging chunked transcripts")
        # Make sure segments are sorted by start time — chunks always land in
        # order but defensive sort is cheap and protects against weird inputs.
        merged_segments.sort(key=lambda s: s.start_seconds)
        return TranscriptResult(
            language=language,
            model_name=f"groq:{GROQ_MODEL}",
            full_text=" ".join(merged_text),
            segments=merged_segments,
        )
