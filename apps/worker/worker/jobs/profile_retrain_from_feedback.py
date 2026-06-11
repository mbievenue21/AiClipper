"""Retrain profile from accumulated user/editor feedback."""

from __future__ import annotations

import json
import time
from typing import Any

import structlog
from sqlalchemy import and_, select

from ..db import session_scope
from ..ids import new_id
from ..models import ClipFeedback, Highlight, ProfileTrainingRun, TrainingExample, TrainingDataset
from ..profile.features import extract_window_features
from ..profile.loader import load_active_profile_version
from ..profile.project_context import load_project_context
from .handlers import ProgressReporter, register
from .pipeline_enqueue import forward_payload

log = structlog.get_logger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _example_exists(
    session,
    *,
    dataset_id: str,
    project_id: str | None,
    start: float,
    end: float,
    label: str,
) -> bool:
    row = session.execute(
        select(TrainingExample.id).where(
            and_(
                TrainingExample.dataset_id == dataset_id,
                TrainingExample.project_id == project_id,
                TrainingExample.start_seconds == start,
                TrainingExample.end_seconds == end,
                TrainingExample.label == label,
            )
        )
    ).scalar_one_or_none()
    return row is not None


@register("profile_retrain_from_feedback")
async def handle_profile_retrain_from_feedback(
    job, progress: ProgressReporter
) -> dict[str, Any]:
    payload = job.payload
    profile_id = payload.get("profile_id")
    dataset_id = payload.get("dataset_id")
    project_id = payload.get("project_id")

    if not profile_id or not dataset_id:
        raise ValueError("profile_retrain_from_feedback requires profile_id and dataset_id")

    skip_import = bool(payload.get("skip_clip_feedback_import"))
    example_only = bool(payload.get("example_only"))

    added = 0
    if not skip_import and not example_only:
        progress(0.2, "collecting feedback examples")
        active = load_active_profile_version(profile_id)

        with session_scope() as session:
            ds = session.get(TrainingDataset, dataset_id)
            if ds is None:
                raise ValueError(f"Dataset {dataset_id} not found")

            query = select(ClipFeedback)
            if project_id:
                query = query.where(ClipFeedback.project_id == project_id)
            feedback_rows = session.execute(query).scalars().all()

            for fb in feedback_rows:
                if not fb.overall_vote:
                    if fb.notes == "editor_trim_save":
                        label = "accepted"
                        reason = "user_trim"
                    else:
                        continue
                else:
                    label = "accepted" if fb.overall_vote == "up" else "rejected"
                    reason = "user_accept" if fb.overall_vote == "up" else "user_reject"

                highlight = session.get(Highlight, fb.highlight_id)
                if highlight is None:
                    continue

                start = float(fb.source_start_seconds or highlight.start_seconds)
                end = float(fb.source_end_seconds or highlight.end_seconds)

                if _example_exists(
                    session,
                    dataset_id=dataset_id,
                    project_id=fb.project_id,
                    start=start,
                    end=end,
                    label=label,
                ):
                    continue

                features_json = None
                try:
                    ctx = load_project_context(fb.project_id)
                    feats = extract_window_features(
                        start_seconds=start,
                        end_seconds=end,
                        segments=ctx.segments,
                        config=active.config,
                        duration_seconds=ctx.duration_seconds,
                        video_path=ctx.video_path,
                        vibe=str(ctx.settings.get("vibe", "") or ""),
                    )
                    features_json = feats.to_dict()
                except Exception:
                    if fb.reason_snapshot_json:
                        try:
                            features_json = json.loads(fb.reason_snapshot_json)
                        except json.JSONDecodeError:
                            pass

                example = TrainingExample(
                    dataset_id=dataset_id,
                    project_id=fb.project_id,
                    start_seconds=start,
                    end_seconds=end,
                    label=label,
                    confidence=1.0,
                    reason=reason,
                )
                if features_json:
                    example.features_json = json.dumps(features_json)
                session.add(example)
                added += 1

            ds.updated_at = _now_ms()

    from . import queue

    training_run_id = new_id()
    with session_scope() as session:
        session.add(
            ProfileTrainingRun(
                id=training_run_id,
                profile_id=profile_id,
                dataset_id=dataset_id,
                status="queued",
                optimizer="optuna",
            )
        )

    queue.enqueue(
        "profile_train",
        forward_payload(
            payload,
            profile_id=profile_id,
            dataset_id=dataset_id,
            training_run_id=training_run_id,
            wait_for_imports=False,
            n_trials=int(payload.get("n_trials", 25)),
        ),
    )

    progress(1.0, f"queued retrain ({added} new examples)")
    return {"feedback_examples_added": added, "training_run_id": training_run_id}
