"""Build training context from feedback projects and reference clips."""

from __future__ import annotations

from ..analyze.audio_features import AudioFeatureSeries, compute_audio_features
from ..analyze.candidates import Segment
from ..analyze.chat_features import ChatDensitySeries, compute_chat_density
from ..db import session_scope
from ..models import Project, TrainingExample, Video
from ..profile.project_context import load_project_context
from sqlalchemy import select


def load_training_context_for_dataset(dataset_id: str):
    """Pick the best project with audio/transcript to ground Optuna scoring."""
    with session_scope() as session:
        examples = (
            session.execute(
                select(TrainingExample)
                .where(TrainingExample.dataset_id == dataset_id)
                .order_by(TrainingExample.created_at.desc())
            )
            .scalars()
            .all()
        )

    project_ids = [e.project_id for e in examples if e.project_id]
    if not project_ids:
        return [], None, None, 0.0

    for pid in dict.fromkeys(project_ids):
        try:
            ctx = load_project_context(pid)
            audio = compute_audio_features(ctx.audio_path)
            chat = compute_chat_density(
                ctx.chat_events,
                duration_seconds=ctx.duration_seconds or audio.duration_seconds,
            )
            return ctx.segments, audio, chat, ctx.duration_seconds or audio.duration_seconds
        except Exception:
            continue

    return [], None, None, 0.0
