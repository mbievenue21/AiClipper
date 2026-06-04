"""Transcribe job handler.

Reads a project's audio.wav (produced by ingest), runs the configured
transcription backend, and persists a `transcripts` row plus one
`transcript_segments` row per utterance.

Status flow when this handler runs:
    project.status = "transcribing"   (set when we start)
    project.status = "ready"          (set when we finish — Step 7 will
                                       insert an "analyzing" state later)
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
from ..models import Project, Transcript, TranscriptSegment, Video
from ..transcribe import transcribe as run_transcription
from . import queue
from .handlers import ProgressReporter, register

log = structlog.get_logger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _set_project_status(session, project: Project, status: str, *, note: str | None = None) -> None:
    project.status = status
    project.updated_at = _now_ms()
    if note is not None:
        project.notes = note


def _resolve_audio_path(audio_relpath: str) -> Path:
    """Convert the DB-relative ``audio_path`` back into an absolute path."""
    root = get_settings().media_root_path
    candidate = root / audio_relpath
    return candidate.resolve()


@register("transcribe")
async def handle_transcribe(job, progress: ProgressReporter) -> dict[str, Any]:
    payload = job.payload
    project_id = payload.get("project_id") or job.project_id
    if not project_id:
        raise ValueError("transcribe job requires project_id")

    log.info("transcribe_start", project_id=project_id)

    # Phase 1: resolve inputs and flip project status to "transcribing".
    with session_scope() as session:
        project = session.get(Project, project_id)
        if project is None:
            raise ValueError(f"Project {project_id!r} not found")

        video = session.execute(
            select(Video).where(Video.project_id == project_id)
        ).scalar_one_or_none()
        if video is None:
            raise ValueError(
                f"Project {project_id} has no video yet — run the ingest job first."
            )
        if not video.audio_path:
            raise ValueError(
                f"Video {video.id} has no audio_path; ingest must extract audio.wav first."
            )

        existing = session.execute(
            select(Transcript).where(Transcript.video_id == video.id)
        ).scalar_one_or_none()
        if existing is not None:
            # Idempotency: don't re-transcribe if we already have one. Still
            # kick off analyze so re-runs cleanly advance the pipeline.
            log.info("transcribe_skipped_existing", transcript_id=existing.id, video_id=video.id)
            _set_project_status(session, project, "analyzing")
            existing_id = existing.id
            existing_video_id = video.id
            # Enqueue outside the session to keep the transaction tight.
            next_job = queue.enqueue(
                "analyze",
                {"project_id": project_id, "video_id": existing_video_id},
                project_id=project_id,
            )
            return {
                "transcript_id": existing_id,
                "video_id": existing_video_id,
                "skipped": True,
                "reason": "transcript already exists",
                "next_job_id": next_job.id,
            }

        _set_project_status(session, project, "transcribing")
        audio_rel = video.audio_path
        video_id = video.id
        duration = video.duration_seconds

    audio_abs = _resolve_audio_path(audio_rel)
    if not audio_abs.exists():
        raise FileNotFoundError(
            f"audio.wav missing on disk for project {project_id}: expected {audio_abs}"
        )

    # Phase 2: run the model. This is CPU/GPU heavy → push to a thread so the
    # asyncio loop keeps serving health checks and progress polls.
    try:
        result = await asyncio.to_thread(
            run_transcription,
            audio_abs,
            duration_seconds=duration,
            progress=progress,
        )
    except Exception as exc:
        log.exception("transcribe_failed", project_id=project_id)
        with session_scope() as session:
            project = session.get(Project, project_id)
            if project is not None:
                _set_project_status(session, project, "failed", note=str(exc)[:2000])
        raise

    # Phase 3: persist transcript + segments and mark project ready.
    progress(0.98, "saving transcript")
    with session_scope() as session:
        # Double-check no one beat us to it (e.g. duplicate retry).
        existing = session.execute(
            select(Transcript).where(Transcript.video_id == video_id)
        ).scalar_one_or_none()
        if existing is not None:
            log.info("transcribe_race_detected", transcript_id=existing.id)
            transcript_id = existing.id
        else:
            transcript = Transcript(
                video_id=video_id,
                language=result.language,
                model=result.model_name,
                full_text=result.full_text,
            )
            session.add(transcript)
            session.flush()

            for seg in result.segments:
                row = TranscriptSegment(
                    transcript_id=transcript.id,
                    start_seconds=seg.start_seconds,
                    end_seconds=seg.end_seconds,
                    text=seg.text,
                )
                row.words = seg.words  # JSON-serialised by the property setter
                session.add(row)

            transcript_id = transcript.id

        project = session.get(Project, project_id)
        if project is not None:
            # Step 7 (analyze) takes it from here.
            _set_project_status(session, project, "analyzing")

    # Chain Step 7: hand off to analyze.
    next_job = queue.enqueue(
        "analyze",
        {"project_id": project_id, "video_id": video_id},
        project_id=project_id,
    )

    progress(1.0, "transcribe complete")
    log.info(
        "transcribe_done",
        project_id=project_id,
        video_id=video_id,
        transcript_id=transcript_id,
        language=result.language,
        segment_count=len(result.segments),
        model=result.model_name,
        next_job_id=next_job.id,
    )

    return {
        "transcript_id": transcript_id,
        "video_id": video_id,
        "language": result.language,
        "model": result.model_name,
        "segment_count": len(result.segments),
        "duration_seconds": duration,
        "next_job_id": next_job.id,
    }
