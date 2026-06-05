"""Analyze job handler.

Reads transcript + audio + chat for a project, runs the analyze pipeline,
and persists ``audio_features``, ``chat_events``, and ``highlights`` rows.

Status flow:
    project.status = "analyzing"   (set when we start)
    project.status = "ready"       (set when we finish)
    project.status = "failed"      (on exception)

This is the last automatic stage of the pipeline today. Step 8 (clip render)
will be triggered by the user clicking "approve" on a highlight in the UI.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select

from ..analyze import AnalysisInput, analyze_project
from ..analyze.candidates import Segment
from ..analyze.chat_features import iter_existing_events, parse_twitch_chat
from ..config import get_settings
from ..db import session_scope
from ..models import (
    AudioFeatures,
    ChatEvent,
    Highlight,
    Project,
    Transcript,
    TranscriptSegment,
    Video,
)
from .handlers import ProgressReporter, register

log = structlog.get_logger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _set_project_status(session, project: Project, status: str, *, note: str | None = None) -> None:
    project.status = status
    project.updated_at = _now_ms()
    if note is not None:
        project.notes = note


def _media_abs(rel_path: str) -> Path:
    return (get_settings().media_root_path / rel_path).resolve()


@register("analyze")
async def handle_analyze(job, progress: ProgressReporter) -> dict[str, Any]:
    payload = job.payload
    project_id = payload.get("project_id") or job.project_id
    if not project_id:
        raise ValueError("analyze job requires project_id")

    log.info("analyze_start", project_id=project_id)

    # Phase 1: gather inputs from the database.
    with session_scope() as session:
        project = session.get(Project, project_id)
        if project is None:
            raise ValueError(f"Project {project_id!r} not found")

        video = session.execute(
            select(Video).where(Video.project_id == project_id)
        ).scalar_one_or_none()
        if video is None or not video.audio_path:
            raise ValueError(
                "analyze requires ingest + transcribe to have completed first."
            )

        transcript = session.execute(
            select(Transcript).where(Transcript.video_id == video.id)
        ).scalar_one_or_none()
        if transcript is None:
            raise ValueError(
                f"No transcript for video {video.id}. Run transcribe first."
            )

        seg_rows = (
            session.execute(
                select(TranscriptSegment)
                .where(TranscriptSegment.transcript_id == transcript.id)
                .order_by(TranscriptSegment.start_seconds)
            )
            .scalars()
            .all()
        )

        existing_events = (
            session.execute(
                select(ChatEvent)
                .where(ChatEvent.video_id == video.id)
                .order_by(ChatEvent.timestamp_seconds)
            )
            .scalars()
            .all()
        )

        settings = project.settings
        audio_rel = video.audio_path
        chat_rel = video.chat_json_path
        duration = float(video.duration_seconds or 0.0)
        language = transcript.language
        video_id = video.id
        existing_audio_features = session.execute(
            select(AudioFeatures).where(AudioFeatures.video_id == video.id)
        ).scalar_one_or_none()
        existing_highlights_count = session.execute(
            select(Highlight).where(Highlight.video_id == video.id)
        ).scalars().all()

        segments = [
            Segment(
                start_seconds=float(s.start_seconds),
                end_seconds=float(s.end_seconds),
                text=s.text or "",
            )
            for s in seg_rows
        ]
        chat_events_internal = iter_existing_events(existing_events)
        already_have_chat_rows = len(chat_events_internal) > 0
        had_audio_features = existing_audio_features is not None
        had_highlights = len(existing_highlights_count) > 0

        _set_project_status(session, project, "analyzing")

    # Parse chat JSON only if we haven't already imported rows for this video.
    new_chat_events_for_db = []
    if not already_have_chat_rows and chat_rel:
        chat_abs = _media_abs(chat_rel)
        if chat_abs.exists():
            new_chat_events_for_db = parse_twitch_chat(chat_abs)
            chat_events_internal = new_chat_events_for_db
            log.info("chat_imported_from_json", count=len(chat_events_internal))

    # Phase 2: run the actual pipeline in a worker thread.
    audio_abs = _media_abs(audio_rel)
    if not audio_abs.exists():
        raise FileNotFoundError(f"audio file missing: {audio_abs}")

    # Per-run model override: when the user triggers "Re-analyze" from the
    # project page they can pick Flash vs Pro for that specific run without
    # touching the saved project settings. The web layer puts the choice in
    # the job payload under `analyze_model_override`.
    analyze_model_override = payload.get("analyze_model_override")
    if analyze_model_override and analyze_model_override in (
        "gemini-2.5-pro",
        "gemini-2.5-flash",
    ):
        chosen_model = str(analyze_model_override)
        log.info(
            "analyze_model_overridden",
            project_id=project_id,
            override=chosen_model,
            saved=settings.get("analyzeModel"),
        )
    else:
        chosen_model = str(settings.get("analyzeModel", "gemini-2.5-pro"))

    # Per-run vibe / creator brief override (re-analyze dialog).
    vibe_override = payload.get("vibe_override")
    if vibe_override is not None:
        chosen_vibe = str(vibe_override).strip()
        log.info(
            "analyze_vibe_overridden",
            project_id=project_id,
            override_len=len(chosen_vibe),
        )
    else:
        chosen_vibe = str(settings.get("vibe", "") or "")

    analysis_input = AnalysisInput(
        audio_path=audio_abs,
        duration_seconds=duration,
        segments=segments,
        chat_events=chat_events_internal,
        language=language,
        top_n=int(settings.get("topN", 3)),
        min_clip_seconds=float(settings.get("minClipSeconds", 20)),
        max_clip_seconds=float(settings.get("maxClipSeconds", 60)),
        vibe=chosen_vibe,
        pre_roll_seconds=float(settings.get("preRollSeconds", 8)),
        tail_padding_seconds=float(settings.get("tailPaddingSeconds", 2)),
        analyze_model=chosen_model,
    )

    try:
        result = await asyncio.to_thread(
            analyze_project, analysis_input, progress=progress
        )
    except Exception as exc:
        log.exception("analyze_failed", project_id=project_id)
        with session_scope() as session:
            project = session.get(Project, project_id)
            if project is not None:
                _set_project_status(session, project, "failed", note=str(exc)[:2000])
        raise

    # Phase 3: persist everything.
    progress(0.97, "saving highlights")
    with session_scope() as session:
        # Audio features (one row per video). Replace any existing row.
        if had_audio_features:
            session.execute(
                select(AudioFeatures).where(AudioFeatures.video_id == video_id)
            ).scalar_one().samples = result.audio_series.samples
        else:
            af = AudioFeatures(video_id=video_id)
            af.samples = result.audio_series.samples
            session.add(af)

        # Newly parsed chat events.
        if new_chat_events_for_db:
            for ev in new_chat_events_for_db:
                session.add(
                    ChatEvent(
                        video_id=video_id,
                        timestamp_seconds=ev.timestamp_seconds,
                        username=ev.username,
                        message=ev.message,
                        emote_count=ev.emote_count,
                        message_type=ev.message_type,
                    )
                )

        # Highlights: clear and re-insert so re-running analyze produces a clean set.
        if had_highlights:
            for h in (
                session.execute(
                    select(Highlight).where(Highlight.video_id == video_id)
                )
                .scalars()
                .all()
            ):
                session.delete(h)
            session.flush()

        for h in result.highlights:
            row = Highlight(
                video_id=video_id,
                start_seconds=h.start_seconds,
                end_seconds=h.end_seconds,
                score=h.score,
                title=h.title,
                summary=h.summary,
                status="candidate",
            )
            row.reason_json = json.dumps(h.to_reason_json())
            session.add(row)

        project = session.get(Project, project_id)
        if project is not None:
            note = None
            if result.notes:
                note = "\n".join(result.notes)[:2000]
            _set_project_status(session, project, "ready", note=note)

    progress(1.0, "analyze complete")
    log.info(
        "analyze_done",
        project_id=project_id,
        highlights=len(result.highlights),
        candidates=len(result.candidates),
        used_llm=result.used_llm,
    )

    return {
        "project_id": project_id,
        "video_id": video_id,
        "highlight_count": len(result.highlights),
        "candidate_count": len(result.candidates),
        "used_llm": result.used_llm,
        "notes": result.notes,
    }
