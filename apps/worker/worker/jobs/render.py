"""Render job: cut + reformat a highlight into a final clip MP4.

Triggered when the user clicks "Render clip" on a highlight. The job:

1. Resolves the highlight + source video + project settings.
2. Snaps start/end to the nearest scene cut (PySceneDetect, +/- 1.5s).
3. Cuts and reformats with ffmpeg (blurred-fill vertical or 1:1, h264).
4. Extracts the dominant frame color (for caption gradients later).
5. Updates the clips row with the final path and a "ready" status.

If the user requested captions in the same render request, we then enqueue
a follow-up ``caption`` job that overlays styled subtitles using libass.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select

from ..config import get_settings
from ..db import session_scope
from ..media.paths import project_dir, rel_path
from ..media.probe import probe_video
from ..models import Clip, Highlight, Video
from ..render import RenderSpec, extract_dominant_color, render_clip, snap_to_scenes
from ..render.ffmpeg import target_resolution
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


@register("render")
async def handle_render(job, progress: ProgressReporter) -> dict[str, Any]:
    payload = job.payload
    highlight_id = payload.get("highlight_id")
    clip_id_existing = payload.get("clip_id")
    auto_caption: bool = bool(payload.get("auto_caption", False))
    caption_style_in: dict[str, Any] | None = payload.get("caption_style") or None

    if not highlight_id:
        raise ValueError("render job requires highlight_id in payload")

    log.info("render_start", highlight_id=highlight_id, clip_id=clip_id_existing)

    # Phase 1: gather everything we need.
    progress(0.02, "loading highlight + source video")
    with session_scope() as session:
        highlight = session.get(Highlight, highlight_id)
        if highlight is None:
            raise ValueError(f"Highlight {highlight_id!r} not found")
        video = session.get(Video, highlight.video_id)
        if video is None:
            raise ValueError("Highlight has no associated video")
        if not video.file_path:
            raise ValueError("Video has no file_path on disk")

        project = video.project
        project_id = project.id
        aspect = str(project.settings.get("aspect", "9:16"))
        source_rel = video.file_path
        source_duration = float(video.duration_seconds or 0.0)
        fps_hint = float(video.fps or 30.0)
        h_start = float(highlight.start_seconds)
        h_end = float(highlight.end_seconds)

        # Re-use existing clip row if the caller passed one (re-render path);
        # otherwise create a fresh one in "rendering" state.
        if clip_id_existing:
            clip = session.get(Clip, clip_id_existing)
            if clip is None:
                raise ValueError(f"Clip {clip_id_existing!r} not found")
            clip.status = "rendering"
            clip.error_message = None
        else:
            clip = Clip(
                highlight_id=highlight_id,
                file_path="",  # filled in after render
                aspect=aspect,
                has_captions=False,
                status="rendering",
            )
            session.add(clip)
            session.flush()
        clip_id = clip.id

    source_abs = _media_abs(source_rel)
    if not source_abs.exists():
        raise FileNotFoundError(f"Source video missing on disk: {source_abs}")

    # Phase 2: scene-snap boundaries.
    progress(0.10, "detecting scene boundaries")
    try:
        snapped_start, snapped_end, moved = await snap_to_scenes(
            source_abs, h_start, h_end, fps_hint=fps_hint
        )
        if moved:
            log.info(
                "render_scene_snapped",
                original=(h_start, h_end),
                snapped=(snapped_start, snapped_end),
            )
    except Exception:
        log.exception("scene_snap_failed_continuing_without")
        snapped_start, snapped_end = h_start, h_end

    # Clamp to source duration just in case.
    if source_duration > 0:
        snapped_start = max(0.0, min(snapped_start, source_duration - 0.5))
        snapped_end = max(snapped_start + 0.5, min(snapped_end, source_duration))

    # Phase 3: render.
    progress(0.20, "rendering with ffmpeg")
    out_dir = _clip_dir(project_id, clip_id)
    out_path = out_dir / "clip.mp4"
    width, height = target_resolution(aspect)
    spec = RenderSpec(
        source_path=source_abs,
        output_path=out_path,
        start_seconds=snapped_start,
        end_seconds=snapped_end,
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

    # Phase 4: probe + dominant color.
    progress(0.85, "probing output + extracting dominant color")
    probe = await asyncio.to_thread(probe_video, out_path)
    dominant_hex: str | None = None
    try:
        dominant_hex = await extract_dominant_color(
            out_path, probe.duration_seconds or spec.duration
        )
    except Exception:
        log.exception("dominant_color_failed_continuing")

    # Phase 5: persist.
    progress(0.95, "saving clip row")
    rel_out = rel_path(out_path)
    with session_scope() as session:
        clip = session.get(Clip, clip_id)
        if clip is None:
            raise RuntimeError(f"Clip {clip_id} disappeared before save")
        clip.file_path = rel_out
        clip.duration_seconds = probe.duration_seconds or spec.duration
        clip.width_px = probe.width or width
        clip.height_px = probe.height or height
        clip.dominant_color = dominant_hex
        clip.aspect = aspect
        clip.source_start_seconds = snapped_start
        clip.source_end_seconds = snapped_end
        clip.status = "ready"
        clip.error_message = None
        clip.updated_at = _now_ms()
        if caption_style_in is not None:
            clip.caption_style = caption_style_in

        # Mark highlight rendered so the UI can collapse the "Render" CTA.
        h = session.get(Highlight, highlight_id)
        if h is not None:
            h.status = "rendered"

    # Phase 6: optional immediate caption pass.
    next_job_id: str | None = None
    if auto_caption:
        progress(0.98, "queueing caption pass")
        next_job = queue.enqueue(
            "caption",
            {
                "clip_id": clip_id,
                "caption_style": caption_style_in or {},
            },
            project_id=project_id,
        )
        next_job_id = next_job.id

    progress(1.0, "render complete")
    log.info(
        "render_done",
        highlight_id=highlight_id,
        clip_id=clip_id,
        file_path=rel_out,
        dominant_color=dominant_hex,
        next_job_id=next_job_id,
    )
    return {
        "clip_id": clip_id,
        "file_path": rel_out,
        "dominant_color": dominant_hex,
        "duration_seconds": probe.duration_seconds,
        "next_job_id": next_job_id,
    }
