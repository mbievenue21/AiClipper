"""Wait until reference clip imports finish before training."""

from __future__ import annotations

import time

from sqlalchemy import func, select

from ..db import session_scope
from ..models import Job, ReferenceClip, TrainingExample


def _pending_dataset_jobs(dataset_id: str) -> int:
    with session_scope() as session:
        jobs = (
            session.execute(
                select(Job).where(
                    Job.type.in_(("reference_clip_import", "reference_feature_extract")),
                    Job.status.in_(("pending", "running")),
                )
            )
            .scalars()
            .all()
        )
        return sum(
            1 for job in jobs if job.payload.get("dataset_id") == dataset_id
        )


def wait_for_dataset_ready(
    dataset_id: str,
    *,
    expected_import_count: int = 0,
    timeout_seconds: int = 600,
    poll_seconds: float = 2.0,
) -> bool:
    """Block until imports/features finish, or timeout.

    When ``expected_import_count`` > 0 we wait for that many reference clips
    and a training example per clip, plus any in-flight import/extract jobs.
    """
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        with session_scope() as session:
            clip_count = session.execute(
                select(func.count())
                .select_from(ReferenceClip)
                .where(ReferenceClip.dataset_id == dataset_id)
            ).scalar_one()

            clip_example_count = session.execute(
                select(func.count())
                .select_from(TrainingExample)
                .where(
                    TrainingExample.dataset_id == dataset_id,
                    TrainingExample.reference_clip_id.is_not(None),
                )
            ).scalar_one()

            feedback_example_count = session.execute(
                select(func.count())
                .select_from(TrainingExample)
                .where(
                    TrainingExample.dataset_id == dataset_id,
                    TrainingExample.reference_clip_id.is_(None),
                )
            ).scalar_one()

        pending_jobs = _pending_dataset_jobs(dataset_id)

        if expected_import_count > 0:
            imports_done = clip_count >= expected_import_count
            features_done = clip_example_count >= clip_count and clip_count > 0
            if imports_done and features_done and pending_jobs == 0:
                return True
        elif clip_count > 0:
            if clip_example_count >= clip_count and pending_jobs == 0:
                return True
        elif feedback_example_count > 0 and pending_jobs == 0:
            return True

        time.sleep(poll_seconds)
    return False
