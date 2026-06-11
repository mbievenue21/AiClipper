"""Enrich training example editor notes with Gemini Flash."""

from __future__ import annotations

import json
from typing import Any

import structlog
from sqlalchemy import select

from ..db import session_scope
from ..models import HighlightProfile, TrainingExample, Transcript, TranscriptSegment, Video
from ..profile.feedback_enrich import enrich_editor_notes
from .handlers import ProgressReporter, register

log = structlog.get_logger(__name__)


def _transcript_excerpt(
    project_id: str | None,
    start: float,
    end: float,
) -> str:
    if not project_id:
        return ""
    with session_scope() as session:
        video = session.execute(
            select(Video).where(Video.project_id == project_id).limit(1)
        ).scalar_one_or_none()
        if video is None:
            return ""
        transcript = session.execute(
            select(Transcript).where(Transcript.video_id == video.id).limit(1)
        ).scalar_one_or_none()
        if transcript is None:
            return ""
        segments = (
            session.execute(
                select(TranscriptSegment)
                .where(TranscriptSegment.transcript_id == transcript.id)
                .where(TranscriptSegment.end_seconds >= start - 5)
                .where(TranscriptSegment.start_seconds <= end + 5)
            )
            .scalars()
            .all()
        )
        return " ".join(s.text for s in segments if s.text).strip()


@register("enrich_training_feedback")
async def handle_enrich_training_feedback(
    job, progress: ProgressReporter
) -> dict[str, Any]:
    payload = job.payload
    example_id = payload.get("training_example_id")
    if not example_id:
        raise ValueError("enrich_training_feedback requires training_example_id")

    progress(0.2, "loading training example")
    with session_scope() as session:
        ex = session.get(TrainingExample, example_id)
        if ex is None:
            raise ValueError(f"Training example {example_id} not found")

        features = ex.features
        notes = features.get("editorNotes")
        if not isinstance(notes, dict):
            return {"skipped": True, "reason": "no_editor_notes"}

        if notes.get("gemini") and not payload.get("force"):
            return {"skipped": True, "reason": "already_enriched"}

        if notes.get("enrichWithGemini") is False:
            return {"skipped": True, "reason": "enrichment_disabled"}

        label = str(ex.label)
        project_id = ex.project_id
        start = float(ex.start_seconds)
        end = float(ex.end_seconds)

        profile = None
        profile_id = payload.get("profile_id")
        if profile_id:
            profile = session.get(HighlightProfile, profile_id)
        game = profile.game if profile else ""

    transcript = _transcript_excerpt(project_id, start, end)
    signal_parts: list[str] = []
    if isinstance(features.get("profileScore"), dict):
        signal_parts.append(str(features["profileScore"].get("explanation", "")))
    if features.get("explanation"):
        signal_parts.append(str(features["explanation"]))

    progress(0.5, "Gemini Flash enrichment")
    enriched = enrich_editor_notes(
        label=label,
        editor_notes=notes,
        transcript_excerpt=transcript,
        signal_summary="; ".join(p for p in signal_parts if p),
        game=game or "",
    )

    if not enriched:
        return {"skipped": True, "reason": "gemini_unavailable"}

    progress(0.9, "saving enrichment")
    with session_scope() as session:
        ex = session.get(TrainingExample, example_id)
        if ex is None:
            raise ValueError("Example disappeared")
        merged = dict(ex.features)
        editor = dict(merged.get("editorNotes") or {})
        editor["gemini"] = enriched
        merged["editorNotes"] = editor
        ex.features = merged

    progress(1.0, "enrichment complete")
    return {"training_example_id": example_id, "enriched": True}
