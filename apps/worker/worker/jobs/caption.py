"""Caption job: layer animated styled captions onto a previously-rendered clip.

Inputs: an existing ``Clip`` row that already has ``file_path`` (a clean
rendered mp4) and the caption style the user picked.

Output: a sibling ``clip-captioned.mp4`` next to the input, plus the
``captioned_file_path`` + ``has_captions`` columns on the clip row.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select

from ..config import get_settings
from ..db import session_scope
from ..media.paths import rel_path
from ..models import Clip, Highlight, Transcript, TranscriptSegment, Video
from ..render.captions import (
    chunk_segments,
    resolve_caption_source_window,
    segments_from_overrides,
    write_ass_file,
)
from ..render.ffmpeg import burn_subtitles
from .handlers import ProgressReporter, register

log = structlog.get_logger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _media_abs(rel_path_str: str) -> Path:
    return (get_settings().media_root_path / rel_path_str).resolve()


@register("caption")
async def handle_caption(job, progress: ProgressReporter) -> dict[str, Any]:
    payload = job.payload
    clip_id = payload.get("clip_id")
    style_in: dict[str, Any] = payload.get("caption_style") or {}

    if not clip_id:
        raise ValueError("caption job requires clip_id in payload")

    log.info("caption_start", clip_id=clip_id)

    # Phase 1: gather inputs.
    progress(0.05, "loading clip + transcript")
    stored_override: list[dict[str, Any]] | None = None
    with session_scope() as session:
        clip = session.get(Clip, clip_id)
        if clip is None:
            raise ValueError(f"Clip {clip_id!r} not found")
        if clip.status not in ("ready", "captioning", "failed"):
            raise ValueError(
                f"Clip {clip_id} is not in a captionable state (status={clip.status!r})"
            )

        highlight = session.get(Highlight, clip.highlight_id)
        if highlight is None:
            raise ValueError("Clip has no associated highlight")
        video = session.get(Video, highlight.video_id)
        if video is None:
            raise ValueError("Highlight has no associated video")
        project_id = video.project_id

        transcript = session.execute(
            select(Transcript).where(Transcript.video_id == video.id)
        ).scalar_one_or_none()
        if transcript is None:
            raise ValueError("Cannot add captions: no transcript for this video.")

        h_start = float(highlight.start_seconds)
        h_end = float(highlight.end_seconds)
        source_start, source_end = resolve_caption_source_window(
            highlight_start=h_start,
            highlight_end=h_end,
            source_start=clip.source_start_seconds,
            source_end=clip.source_end_seconds,
            trim_start=clip.trim_start_seconds,
            trim_end=clip.trim_end_seconds,
        )

        seg_rows = (
            session.execute(
                select(TranscriptSegment)
                .where(TranscriptSegment.transcript_id == transcript.id)
                .where(TranscriptSegment.end_seconds >= source_start)
                .where(TranscriptSegment.start_seconds <= source_end)
                .order_by(TranscriptSegment.start_seconds)
            )
            .scalars()
            .all()
        )

        raw_segments = [
            {
                "start_seconds": float(s.start_seconds),
                "end_seconds": float(s.end_seconds),
                "text": s.text or "",
                "words": s.words,
            }
            for s in seg_rows
        ]

        # Merge incoming style overrides onto whatever is already stored.
        current_style = clip.caption_style
        merged_style: dict[str, Any] = {**current_style, **(style_in or {})}

        clip_input_path_rel = clip.file_path
        width_px = int(clip.width_px or 1080)
        height_px = int(clip.height_px or 1920)
        dominant_hex = clip.dominant_color
        clip.status = "captioning"
        clip.error_message = None
        clip.caption_style = merged_style
        clip.updated_at = _now_ms()

        stored_override = None
        if clip.caption_segments_json:
            try:
                stored_override = json.loads(clip.caption_segments_json)
            except json.JSONDecodeError:
                stored_override = None

    clip_input_abs = _media_abs(clip_input_path_rel)
    if not clip_input_abs.exists():
        raise FileNotFoundError(f"Clip file missing: {clip_input_abs}")

    # Phase 2: chunk transcript into caption segments scoped to the clip.
    progress(0.30, "chunking transcript into caption lines")
    # Short-form line length: ~6–8 words per line, max 2 lines.
    if width_px < height_px:
        chars_per_line = 18
    elif width_px == height_px:
        chars_per_line = 24
    else:
        chars_per_line = 32
    # Propagate to build_ass so font-size auto-sizing uses the same number.
    merged_style = {**merged_style, "maxCharsPerLine": chars_per_line}

    override_payload = payload.get("caption_segments")
    if override_payload:
        segments = segments_from_overrides(override_payload)
    elif stored_override:
        segments = segments_from_overrides(stored_override)
    else:
        segments = chunk_segments(
            raw_segments,
            clip_start=source_start,
            clip_end=source_end,
            max_chars_per_line=chars_per_line,
        )

    if not segments:
        log.warning("caption_no_segments_in_range", clip_id=clip_id)
        # We still want to return — just write an empty captioned file that
        # equals the source so the UI can show "no speech in this window".
        with session_scope() as session:
            clip = session.get(Clip, clip_id)
            if clip is not None:
                clip.status = "ready"
                clip.has_captions = False
                clip.updated_at = _now_ms()
        return {"clip_id": clip_id, "segments": 0, "captioned_path": None}

    # Phase 3: write .ass + burn it in.
    progress(0.45, "rendering caption styles")
    out_dir = clip_input_abs.parent
    ass_path = out_dir / "captions.ass"
    write_ass_file(
        ass_path,
        segments,
        style=merged_style,
        dominant_color=dominant_hex,
        width_px=width_px,
        height_px=height_px,
    )

    captioned_abs = out_dir / "clip-captioned.mp4"
    progress(0.55, "burning captions with ffmpeg")
    try:
        await burn_subtitles(clip_input_abs, ass_path, captioned_abs)
    except Exception as exc:
        with session_scope() as session:
            clip = session.get(Clip, clip_id)
            if clip is not None:
                clip.status = "failed"
                clip.error_message = str(exc)[:2000]
                clip.updated_at = _now_ms()
        raise

    # Phase 4: persist.
    progress(0.95, "saving captioned path")
    rel_captioned = rel_path(captioned_abs)
    with session_scope() as session:
        clip = session.get(Clip, clip_id)
        if clip is None:
            raise RuntimeError(f"Clip {clip_id} disappeared before save")
        clip.captioned_file_path = rel_captioned
        clip.has_captions = True
        clip.status = "ready"
        clip.error_message = None
        clip.caption_style = merged_style
        clip.updated_at = _now_ms()

    progress(1.0, "captions complete")
    log.info(
        "caption_done",
        clip_id=clip_id,
        captioned_path=rel_captioned,
        style=merged_style.get("style"),
        font=merged_style.get("font"),
    )
    return {
        "clip_id": clip_id,
        "captioned_path": rel_captioned,
        "segments": len(segments),
        "style": merged_style,
    }
