"""Profile training job — Optuna config optimization + optional LightGBM ranker."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog
from sqlalchemy import select

from ..db import session_scope
from ..ids import new_id
from ..models import (
    HighlightProfile,
    HighlightProfileVersion,
    ProfileTrainingRun,
    TrainingExample,
)
from ..config import get_settings
from ..profile.cleanup import cleanup_dataset_media
from ..profile.dataset_ready import wait_for_dataset_ready
from ..profile.features import extract_window_features
from ..profile.config_learn import merge_editor_notes_into_config
from ..profile.loader import load_active_profile_version
from ..profile.optimize import OptimizationResult, TrainingExample as OptExample, optimize_profile_config
from ..profile.ranker import RankerArtifact, train_ranker
from ..profile.training_context import load_training_context_for_dataset
from .handlers import ProgressReporter, register

log = structlog.get_logger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _label_positive(label: str) -> bool:
    return label in ("positive", "accepted", "published")


@register("profile_train")
async def handle_profile_train(job, progress: ProgressReporter) -> dict[str, Any]:
    payload = job.payload
    profile_id = payload.get("profile_id")
    dataset_id = payload.get("dataset_id")
    training_run_id = payload.get("training_run_id")
    n_trials = int(payload.get("n_trials", 40))

    if not profile_id or not dataset_id:
        raise ValueError("profile_train requires profile_id and dataset_id")

    with session_scope() as session:
        run = None
        if training_run_id:
            run = session.get(ProfileTrainingRun, training_run_id)
        if run is None:
            run = ProfileTrainingRun(
                id=training_run_id or new_id(),
                profile_id=profile_id,
                dataset_id=dataset_id,
                status="running",
                optimizer="optuna",
            )
            session.add(run)
        else:
            run.status = "running"
        run_id = run.id

    if payload.get("wait_for_imports", True):
        progress(0.05, "waiting for reference clip imports")
        expected_imports = int(payload.get("expected_import_count") or 0)
        ready = await asyncio.to_thread(
            wait_for_dataset_ready,
            dataset_id,
            expected_import_count=expected_imports,
            timeout_seconds=600,
        )
        if not ready:
            log.warning("dataset_import_timeout", dataset_id=dataset_id)

    progress(0.1, "loading training examples")
    with session_scope() as session:
        examples = (
            session.execute(
                select(TrainingExample).where(
                    TrainingExample.dataset_id == dataset_id
                )
            )
            .scalars()
            .all()
        )

    positives = [
        OptExample(
            start_seconds=float(e.start_seconds),
            end_seconds=float(e.end_seconds),
            label=e.label,
        )
        for e in examples
        if _label_positive(e.label)
    ]
    negatives = [
        OptExample(
            start_seconds=float(e.start_seconds),
            end_seconds=float(e.end_seconds),
            label=e.label,
        )
        for e in examples
        if e.label in ("negative", "rejected")
    ]

    if not positives:
        raise ValueError("Training requires at least one positive example.")

    segments, audio, chat, duration = await asyncio.to_thread(
        load_training_context_for_dataset, dataset_id
    )

    active = load_active_profile_version(profile_id)
    base_config = merge_editor_notes_into_config(active.config, examples)

    progress(0.3, f"optimizing config ({n_trials} trials)")
    result: OptimizationResult = await asyncio.to_thread(
        optimize_profile_config,
        base_config,
        positives,
        negatives,
        segments,
        audio,
        chat,
        n_trials=n_trials,
    )

    progress(0.6, "training supervised ranker")
    ranker_rows: list[tuple[Any, int]] = []
    for ex in examples:
        feats = extract_window_features(
            start_seconds=float(ex.start_seconds),
            end_seconds=float(ex.end_seconds),
            segments=segments,
            audio=audio,
            chat=chat,
            config=result.config,
            duration_seconds=duration,
        )
        ranker_rows.append((feats, 1 if _label_positive(ex.label) else 0))

    ranker: RankerArtifact | None = None
    version_id = ""
    next_version = 0

    progress(0.85, "updating live profile config")
    with session_scope() as session:
        profile = session.get(HighlightProfile, profile_id)
        if profile is None:
            raise ValueError(f"Profile {profile_id} not found")

        live: HighlightProfileVersion | None = None
        if profile.active_version_id:
            live = session.get(HighlightProfileVersion, profile.active_version_id)

        if live is None:
            live = session.execute(
                select(HighlightProfileVersion)
                .where(HighlightProfileVersion.profile_id == profile_id)
                .order_by(
                    HighlightProfileVersion.is_active.desc(),
                    HighlightProfileVersion.version_number.desc(),
                )
            ).scalar_one_or_none()

        ranker: RankerArtifact | None = await asyncio.to_thread(
            train_ranker,
            ranker_rows,
            profile_id=profile_id,
            artifact_key="active",
            model_type="lightgbm_ranker",
        )

        metrics = dict(result.metrics)
        if ranker:
            metrics["rankerType"] = ranker.model_type

        if live is None:
            version_id = new_id()
            live = HighlightProfileVersion(
                id=version_id,
                profile_id=profile_id,
                version_number=1,
                model_type=ranker.model_type if ranker else "config_only",
                is_active=True,
                training_dataset_id=dataset_id,
            )
            metrics["trainingRevision"] = 1
            session.add(live)
            next_version = 1
        else:
            version_id = live.id
            next_version = live.version_number
            prev = live.metrics or {}
            metrics["trainingRevision"] = int(prev.get("trainingRevision") or 0) + 1
            live.model_type = ranker.model_type if ranker else live.model_type
            live.training_dataset_id = dataset_id

        for v in (
            session.execute(
                select(HighlightProfileVersion).where(
                    HighlightProfileVersion.profile_id == profile_id
                )
            )
            .scalars()
            .all()
        ):
            v.is_active = v.id == live.id

        live.config = result.config.to_dict()
        live.metrics = metrics
        live.is_active = True
        if ranker:
            live.model_artifact_path = str(
                ranker.path.relative_to(get_settings().media_root_path)
            ).replace("\\", "/")

        profile.active_version_id = live.id
        profile.status = "active"
        profile.updated_at = _now_ms()

        run = session.get(ProfileTrainingRun, run_id)
        if run:
            run.status = "completed"
            run.result_config = result.config.to_dict()
            run.metrics = metrics
            run.completed_at = _now_ms()

    if payload.get("cleanup_after_train", True):
        progress(0.95, "freeing training media")
        cleanup_dataset_media(dataset_id)

    progress(1.0, "profile training complete")

    from . import queue

    queue.enqueue(
        "profile_evaluate",
        {
            "profile_id": profile_id,
            "version_id": version_id,
            "dataset_id": dataset_id,
        },
    )

    return {
        "profile_id": profile_id,
        "version_id": version_id,
        "version_number": next_version,
        "metrics": result.metrics,
        "trial_count": result.trial_count,
        "ranker": ranker.model_type if ranker else None,
    }
