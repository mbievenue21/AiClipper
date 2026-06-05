"""Reedit job: trim clip from source + re-burn captions with user overrides."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import structlog

from ..config import get_settings
from ..db import session_scope
from ..media.paths import project_dir, rel_path
from ..media.probe import probe_video
from ..models import Clip, Highlight, Video
from ..render import RenderSpec, extract_dominant_color, render_clip
from ..render.captions import segments_from_overrides, write_ass_file
from ..render.ffmpeg import burn_subtitles, target_resolution
from . import queue
from .handlers import ProgressReporter, register

log = structlog.get_logger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _media_abs(rel_path_str: str) -> Path:
    return (get_settings().media_root_path / rel_path_str).resolve()


def _clip_dir(project_id: str, clip_id: str) -> Path:
    p = project_dir(project_id) / "clips" / clip_id
    p.mkdir(parents=True, exist_ok=True)
    return p


@register("reedit")
async def handle_reedit(job, progress: ProgressReporter) -> dict[str, Any]:
    payload = job.payload
    parent_clip_id = payload.get("parent_clip_id") or payload.get("clip_id")
    replace_original = bool(payload.get("replace_original", False))
    trim_start = float(payload.get("trim_start", 0.0))
    trim_end = float(payload.get("trim_end", 0.0))
    caption_segments = payload.get("caption_segments") or []
    caption_style_in: dict[str, Any] = payload.get("caption_style") or {}
    burn_captions = bool(payload.get("burn_captions", True))

    if not parent_clip_id:
        raise ValueError("reedit job requires parent_clip_id or clip_id")

    log.info(
        "reedit_start",
        parent_clip_id=parent_clip_id,
        replace_original=replace_original,
        trim_start=trim_start,
        trim_end=trim_end,
    )

    progress(0.05, "loading clip + highlight")
    with session_scope() as session:
        parent = session.get(Clip, parent_clip_id)
        if parent is None:
            raise ValueError(f"Clip {parent_clip_id!r} not found")
        highlight = session.get(Highlight, parent.highlight_id)
        if highlight is None:
            raise ValueError("Clip has no highlight")
        video = session.get(Video, highlight.video_id)
        if video is None or not video.file_path:
            raise ValueError("No source video")
        project_id = video.project_id
        aspect = parent.aspect
        source_rel = video.file_path
        h_start = float(highlight.start_seconds)
        h_end = float(highlight.end_seconds)
        parent_style = {**parent.caption_style, **caption_style_in}

        if replace_original:
            target_clip = parent
            target_clip.status = "rendering"
            target_clip.error_message = None
            target_clip.updated_at = _now_ms()
            clip_id = parent.id
        else:
            target_clip = Clip(
                highlight_id=parent.highlight_id,
                file_path="",
                aspect=aspect,
                has_captions=False,
                status="rendering",
                parent_clip_id=parent_clip_id,
                version_label=payload.get("version_label"),
                is_active=True,
            )
            session.add(target_clip)
            session.flush()
            clip_id = target_clip.id
            parent.is_active = False

    cut_start = h_start + max(0.0, trim_start)
    cut_end = h_end - max(0.0, trim_end)
    if cut_end <= cut_start + 1.0:
        raise ValueError("Trim range too short — clip must be at least 1 second.")

    source_abs = _media_abs(source_rel)
    if not source_abs.exists():
        raise FileNotFoundError(f"Source video missing: {source_abs}")

    progress(0.20, "re-cutting from source")
    out_dir = _clip_dir(project_id, clip_id)
    out_path = out_dir / "clip.mp4"
    width, height = target_resolution(aspect)
    spec = RenderSpec(
        source_path=source_abs,
        output_path=out_path,
        start_seconds=cut_start,
        end_seconds=cut_end,
        aspect=aspect,
        width=width,
        height=height,
    )
    try:
        await render_clip(spec)
    except Exception as exc:
        with session_scope() as session:
            clip = session.get(Clip, clip_id)
            if clip is not None:
                clip.status = "failed"
                clip.error_message = str(exc)[:2000]
                clip.updated_at = _now_ms()
        raise

    progress(0.70, "probing output")
    probe = await asyncio.to_thread(probe_video, out_path)
    dominant_hex: str | None = None
    try:
        dominant_hex = await extract_dominant_color(
            out_path, probe.duration_seconds or spec.duration
        )
    except Exception:
        log.exception("dominant_color_failed_continuing")

    rel_out = rel_path(out_path)
    progress(0.80, "saving clip metadata")
    with session_scope() as session:
        clip = session.get(Clip, clip_id)
        if clip is None:
            raise RuntimeError(f"Clip {clip_id} disappeared")
        clip.file_path = rel_out
        clip.duration_seconds = probe.duration_seconds or spec.duration
        clip.width_px = probe.width or width
        clip.height_px = probe.height or height
        clip.dominant_color = dominant_hex
        clip.trim_start_seconds = trim_start
        clip.trim_end_seconds = trim_end
        clip.source_start_seconds = cut_start
        clip.source_end_seconds = cut_end
        clip.caption_segments_json = json.dumps(caption_segments)
        clip.caption_style = parent_style
        clip.status = "ready"
        clip.updated_at = _now_ms()

        if replace_original:
            old_captioned = clip.captioned_file_path
            if old_captioned:
                old_abs = _media_abs(old_captioned)
                if old_abs.exists():
                    try:
                        old_abs.unlink()
                    except OSError:
                        pass
            clip.captioned_file_path = None
            clip.has_captions = False

    caption_job_id: str | None = None
    if burn_captions and caption_segments:
        progress(0.90, "burning captions")
        with session_scope() as session:
            clip = session.get(Clip, clip_id)
            if clip is not None:
                clip.status = "captioning"
                clip.updated_at = _now_ms()

        segments = segments_from_overrides(caption_segments)
        ass_path = out_dir / "captions.ass"
        write_ass_file(
            ass_path,
            segments,
            style=parent_style,
            dominant_color=dominant_hex,
            width_px=width,
            height_px=height,
        )
        captioned_abs = out_dir / "clip-captioned.mp4"
        await burn_subtitles(out_path, ass_path, captioned_abs)
        rel_captioned = rel_path(captioned_abs)

        with session_scope() as session:
            clip = session.get(Clip, clip_id)
            if clip is not None:
                clip.captioned_file_path = rel_captioned
                clip.has_captions = True
                clip.status = "ready"
                clip.updated_at = _now_ms()
    elif burn_captions:
        next_job = queue.enqueue(
            "caption",
            {
                "clip_id": clip_id,
                "caption_style": parent_style,
                "caption_segments": caption_segments,
            },
            project_id=project_id,
        )
        caption_job_id = next_job.id

    progress(1.0, "reedit complete")
    log.info("reedit_done", clip_id=clip_id, replace_original=replace_original)
    return {
        "clip_id": clip_id,
        "parent_clip_id": parent_clip_id,
        "file_path": rel_out,
        "replace_original": replace_original,
        "caption_job_id": caption_job_id,
    }
