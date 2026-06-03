"""Job queue operations backed by the `jobs` table in SQLite.

A "job" is a unit of work the worker will execute (ingest, transcribe, etc.).
Both the Next.js app and the worker itself can enqueue jobs; only the worker
claims and runs them.

Claiming is done in a transaction:
    BEGIN IMMEDIATE
    SELECT id FROM jobs WHERE status='pending' AND ... LIMIT 1
    UPDATE jobs SET status='running', started_at=? WHERE id=?
    COMMIT

SQLite + WAL gives us atomic claim semantics for a single worker. (For
multi-worker setups we'd switch to Postgres or add SELECT ... FOR UPDATE,
but for personal use one worker is correct and simplest.)
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import and_, or_, select, update

from ..db import session_scope
from ..models import Job


def _now_ms() -> int:
    return int(time.time() * 1000)


def enqueue(
    job_type: str,
    payload: dict[str, Any],
    *,
    project_id: str | None = None,
    depends_on: str | None = None,
    max_attempts: int = 3,
) -> Job:
    """Insert a new pending job and return it.  Safe to call from anywhere."""
    with session_scope() as session:
        job = Job(
            type=job_type,
            project_id=project_id,
            depends_on_job_id=depends_on,
            max_attempts=max_attempts,
            status="pending",
        )
        job.payload = payload
        session.add(job)
        session.flush()
        session.refresh(job)
        session.expunge(job)
        return job


def claim_next() -> Job | None:
    """Atomically grab the next runnable job. Returns None if none are ready."""
    with session_scope() as session:
        # Sub-query for jobs whose dependency (if any) is already succeeded.
        ready_dep_subq = select(Job.id).where(
            or_(
                Job.depends_on_job_id.is_(None),
                Job.depends_on_job_id.in_(
                    select(Job.id).where(Job.status == "succeeded")
                ),
            )
        )

        next_job = session.execute(
            select(Job)
            .where(and_(Job.status == "pending", Job.id.in_(ready_dep_subq)))
            .order_by(Job.created_at.asc())
            .limit(1)
        ).scalar_one_or_none()

        if next_job is None:
            return None

        session.execute(
            update(Job)
            .where(Job.id == next_job.id)
            .values(
                status="running",
                started_at=_now_ms(),
                attempts=Job.attempts + 1,
                progress=0.0,
                progress_message="starting",
            )
        )
        session.flush()
        session.refresh(next_job)
        session.expunge(next_job)
        return next_job


def update_progress(job_id: str, progress: float, message: str | None = None) -> None:
    with session_scope() as session:
        session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(progress=max(0.0, min(1.0, progress)), progress_message=message)
        )


def mark_succeeded(job_id: str, result: dict[str, Any] | None = None) -> None:
    with session_scope() as session:
        session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                status="succeeded",
                progress=1.0,
                progress_message="done",
                result_json=_dumps(result),
                finished_at=_now_ms(),
            )
        )


def mark_failed(job_id: str, error: str, *, retryable: bool = True) -> None:
    """Mark a job failed. If retryable and attempts < max_attempts, re-enqueues it."""
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        if retryable and job.attempts < job.max_attempts:
            job.status = "pending"
            job.progress = 0.0
            job.progress_message = f"retry pending: {error[:80]}"
            job.error_message = error
            job.finished_at = None
        else:
            job.status = "failed"
            job.progress_message = "failed"
            job.error_message = error
            job.finished_at = _now_ms()


def _dumps(value: dict[str, Any] | None) -> str | None:
    import json

    return json.dumps(value) if value is not None else None
