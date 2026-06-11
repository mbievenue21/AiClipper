"""Evaluate an active profile version against training examples."""

from __future__ import annotations

import time
from typing import Any

import structlog
from sqlalchemy import select

from ..db import session_scope
from ..models import HighlightProfileVersion, TrainingExample
from ..profile.config import load_config_dict
from ..profile.optimize import _evaluate_config, TrainingExample as OptExample
from ..profile.scorer import score_candidate
from ..profile.candidates import ProfileCandidate
from .handlers import ProgressReporter, register

log = structlog.get_logger(__name__)


@register("profile_evaluate")
async def handle_profile_evaluate(job, progress: ProgressReporter) -> dict[str, Any]:
    payload = job.payload
    profile_id = payload.get("profile_id")
    version_id = payload.get("version_id")
    dataset_id = payload.get("dataset_id")

    if not profile_id:
        raise ValueError("profile_evaluate requires profile_id")

    with session_scope() as session:
        if version_id:
            version = session.get(HighlightProfileVersion, version_id)
        else:
            version = session.execute(
                select(HighlightProfileVersion)
                .where(
                    HighlightProfileVersion.profile_id == profile_id,
                    HighlightProfileVersion.is_active.is_(True),
                )
                .order_by(HighlightProfileVersion.version_number.desc())
            ).scalar_one_or_none()

        if version is None:
            raise ValueError("No profile version to evaluate")

        config = load_config_dict(version.config)
        ds_id = dataset_id or version.training_dataset_id
        examples = []
        if ds_id:
            examples = (
                session.execute(
                    select(TrainingExample).where(
                        TrainingExample.dataset_id == ds_id
                    )
                )
                .scalars()
                .all()
            )

    positives = [
        OptExample(float(e.start_seconds), float(e.end_seconds), e.label)
        for e in examples
        if e.label in ("positive", "accepted", "published")
    ]
    negatives = [
        OptExample(float(e.start_seconds), float(e.end_seconds), e.label)
        for e in examples
        if e.label in ("negative", "rejected")
    ]

    progress(0.5, "evaluating metrics")
    metrics = _evaluate_config(config, positives, negatives, [], None, None)

    with session_scope() as session:
        version = session.get(HighlightProfileVersion, version.id)
        if version:
            version.metrics = metrics

    progress(1.0, "evaluation complete")
    return {"version_id": version.id, "metrics": metrics}
