"""Job queue operations backed by the `jobs` table in SQLite.

A "job" is a unit of work the worker will execute (ingest, transcribe, etc.).
Both the Next.js app and the worker itself can enqueue jobs; only the worker
claims and runs them.

Claiming uses a SINGLE statement: `UPDATE ... WHERE id = (SELECT ... LIMIT 1) RETURNING id`.
SQLite executes that as one atomic operation, so even when two worker processes
overlap (e.g. uvicorn --reload spawning a new child before the old one exits),
they can never both claim the same row.
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import text, update

from ..db import session_scope
from ..models import Job

_CLAIM_SQL = text(
    """
    UPDATE jobs
    SET status = 'running',
        started_at = :now,
        attempts = attempts + 1,
        progress = 0.0,
        progress_message = 'starting'
    WHERE id = (
      SELECT id FROM jobs
      WHERE status = 'pending'
        AND (
          depends_on_job_id IS NULL
          OR depends_on_job_id IN (SELECT id FROM jobs WHERE status = 'succeeded')
        )
      ORDER BY created_at ASC
      LIMIT 1
    )
    RETURNING id
    """
)


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
        row = session.execute(_CLAIM_SQL, {"now": _now_ms()}).first()
        if row is None:
            return None
        job = session.get(Job, row[0])
        if job is None:
            return None
        session.expunge(job)
        return job


def reset_stuck_running_jobs() -> int:
    """Reset any jobs left in `running` from a previous worker crash/restart.

    Called at runner startup. With uvicorn --reload, the previous child process
    can die mid-job, leaving the row stuck as `running`. Without this, the
    job would never retry and the project would appear hung.
    """
    with session_scope() as session:
        result = session.execute(
            update(Job)
            .where(Job.status == "running")
            .values(
                status="pending",
                progress=0.0,
                progress_message="reset after worker restart",
                started_at=None,
            )
        )
        return result.rowcount or 0


_HEAL_ORPHAN_PROJECTS_SQL = text(
    """
    UPDATE projects
    SET status = 'failed',
        notes  = COALESCE(NULLIF(notes,''), '') ||
                 CASE WHEN notes IS NULL OR notes = '' THEN '' ELSE char(10) END ||
                 :note,
        updated_at = :now
    WHERE id IN (
      SELECT p.id FROM projects p
      LEFT JOIN jobs j ON j.project_id = p.id
      WHERE p.status IN ('pending','ingesting','transcribing','analyzing')
      GROUP BY p.id
      HAVING SUM(CASE WHEN j.status IN ('pending','running') THEN 1 ELSE 0 END) = 0
         AND SUM(CASE WHEN j.status = 'succeeded' THEN 1 ELSE 0 END) = 0
    )
    """
)


def heal_orphan_projects() -> int:
    """Mark projects stuck mid-pipeline with no active jobs as ``failed``.

    Called at runner startup. Covers the case where the worker died between
    inserting a project and enqueueing the ingest job (or any later stage).
    """
    with session_scope() as session:
        result = session.execute(
            _HEAL_ORPHAN_PROJECTS_SQL,
            {
                "now": _now_ms(),
                "note": (
                    "Worker was unavailable when this stage ran. "
                    "Delete this project and create a new one."
                ),
            },
        )
        return result.rowcount or 0


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
