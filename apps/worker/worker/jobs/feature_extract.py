"""Feature extraction job — audio, chat, scene cuts."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import structlog
from sqlalchemy import select

from ..config import get_settings
from ..analyze.audio_features import compute_audio_features
from ..analyze.chat_features import compute_chat_density, parse_twitch_chat
from ..db import session_scope
from ..models import AudioFeatures, ChatEvent, ChatFeatures, Project, Video
from ..pipeline_report import merge_flow_report
from ..pipeline_timing import ensure_run_id, record_stage
from ..profile.project_context import load_project_context, media_abs
from ..profile.scene_detect import detect_scene_cuts_full
from .handlers import ProgressReporter, register
from .pipeline_enqueue import forward_payload

log = structlog.get_logger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


@register("feature_extract")
async def handle_feature_extract(job, progress: ProgressReporter) -> dict[str, Any]:
    payload = job.payload
    project_id = payload.get("project_id") or job.project_id
    if not project_id:
        raise ValueError("feature_extract requires project_id")

    pipeline_run_id = ensure_run_id(project_id, payload)
    ctx = load_project_context(project_id)

    with session_scope() as session:
        project = session.get(Project, project_id)
        if project is not None:
            project.status = "analyzing"
            project.updated_at = _now_ms()

    progress(0.1, "computing audio features")
    t0 = time.perf_counter()
    audio_series = await asyncio.to_thread(
        compute_audio_features, ctx.audio_path
    )
    audio_ms = int((time.perf_counter() - t0) * 1000)

    progress(0.4, "computing chat density")
    t0 = time.perf_counter()
    chat_density = compute_chat_density(
        ctx.chat_events, duration_seconds=ctx.duration_seconds or audio_series.duration_seconds
    )
    chat_ms = int((time.perf_counter() - t0) * 1000)

    scene_cuts = list(ctx.scene_cuts)
    scene_ms = 0
    if not scene_cuts and ctx.video_path and ctx.video_path.exists():
        progress(0.6, "detecting scene cuts")
        t0 = time.perf_counter()
        scene_cuts = await asyncio.to_thread(
            detect_scene_cuts_full, ctx.video_path
        )
        scene_ms = int((time.perf_counter() - t0) * 1000)

    if get_settings().whisperx_enabled and ctx.audio_path.exists():
        progress(0.75, "WhisperX alignment (optional)")
        try:
            from ..profile.whisperx_align import align_transcript_segments

            seg_dicts = [
                {
                    "start": s.start_seconds,
                    "end": s.end_seconds,
                    "text": s.text,
                }
                for s in ctx.segments
            ]
            aligned = await asyncio.to_thread(
                align_transcript_segments, ctx.audio_path, seg_dicts
            )
            if aligned:
                log.info("whisperx_aligned_segments", count=len(aligned))
        except Exception as exc:
            log.warning("whisperx_skipped", error=str(exc))

    progress(0.85, "saving features")
    with session_scope() as session:
        video = session.get(Video, ctx.video_id)
        if video is None:
            raise ValueError(f"Video {ctx.video_id} not found")

        af = session.execute(
            select(AudioFeatures).where(AudioFeatures.video_id == ctx.video_id)
        ).scalar_one_or_none()
        if af:
            af.samples = audio_series.samples
        else:
            session.add(
                AudioFeatures(video_id=ctx.video_id, samples=audio_series.samples)
            )

        cf = session.execute(
            select(ChatFeatures).where(ChatFeatures.video_id == ctx.video_id)
        ).scalar_one_or_none()
        density_payload = {
            "rawPerSecond": chat_density.raw_per_second,
            "normalised": chat_density.normalised,
            "totalMessages": chat_density.total_messages,
        }
        if cf:
            cf.density_json = json.dumps(density_payload)
        else:
            session.add(
                ChatFeatures(
                    video_id=ctx.video_id,
                    density_json=json.dumps(density_payload),
                )
            )

        if scene_cuts:
            video.scene_cuts_json = json.dumps(scene_cuts)

        if ctx.chat_json_path and ctx.chat_json_path.exists():
            existing = (
                session.execute(
                    select(ChatEvent).where(ChatEvent.video_id == ctx.video_id)
                )
                .scalars()
                .all()
            )
            if not existing:
                for ev in parse_twitch_chat(ctx.chat_json_path):
                    session.add(
                        ChatEvent(
                            video_id=ctx.video_id,
                            timestamp_seconds=ev.timestamp_seconds,
                            username=ev.username,
                            message=ev.message,
                            emote_count=ev.emote_count,
                            message_type=ev.message_type,
                        )
                    )

    record_stage(
        run_id=pipeline_run_id,
        project_id=project_id,
        stage="feature_extract",
        duration_ms=audio_ms + chat_ms + scene_ms,
        started_at=_now_ms() - audio_ms - chat_ms - scene_ms,
        finished_at=_now_ms(),
        status="ok",
        job_id=job.id,
        meta={
            "audio_ms": audio_ms,
            "chat_ms": chat_ms,
            "scene_ms": scene_ms,
            "scene_cut_count": len(scene_cuts),
        },
    )

    merge_flow_report(
        project_id,
        stage="feature_extract",
        data={
            "status": "ok",
            "sceneCutCount": len(scene_cuts),
            "chatAvailable": bool(ctx.chat_events),
            "stageTimingsMs": {
                "librosa_audio": audio_ms,
                "chat_density": chat_ms,
                "scene_detect": scene_ms,
            },
        },
        pipeline_run_id=pipeline_run_id,
    )

    from . import queue

    queue.enqueue(
        "candidate_generate",
        forward_payload(payload, project_id=project_id, video_id=ctx.video_id),
        project_id=project_id,
    )

    progress(1.0, "feature extract complete")
    return {
        "project_id": project_id,
        "scene_cut_count": len(scene_cuts),
        "pipeline_run_id": pipeline_run_id,
    }
