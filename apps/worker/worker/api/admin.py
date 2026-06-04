"""Admin endpoints used by the web UI's /admin page.

These are DB-only operations — they can be triggered while the worker is
running. They CANNOT kill the worker process itself; that's what
``pnpm worker:reset`` is for.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select, update

from ..db import session_scope
from ..jobs import queue
from ..models import Job, Project

router = APIRouter(prefix="/admin", tags=["admin"])


class ResetResult(BaseModel):
    jobs_reset: int = 0
    projects_healed: int = 0
    pending_cancelled: int = 0


class StatsOut(BaseModel):
    jobs_by_status: dict[str, int]
    projects_by_status: dict[str, int]
    oldest_pending_age_s: float | None
    oldest_running_age_s: float | None


@router.post("/heal", response_model=ResetResult)
def heal() -> ResetResult:
    """Reset stuck running jobs + mark orphan projects failed."""
    jobs_reset = queue.reset_stuck_running_jobs()
    projects_healed = queue.heal_orphan_projects()
    return ResetResult(jobs_reset=jobs_reset, projects_healed=projects_healed)


@router.post("/cancel-pending", response_model=ResetResult)
def cancel_pending() -> ResetResult:
    """Cancel every pending job. Use when you want to abort the queue."""
    with session_scope() as session:
        result = session.execute(
            update(Job)
            .where(Job.status == "pending")
            .values(status="cancelled", progress_message="cancelled by admin")
        )
        return ResetResult(pending_cancelled=result.rowcount or 0)


@router.get("/stats", response_model=StatsOut)
def stats() -> StatsOut:
    import time

    now_ms = int(time.time() * 1000)

    with session_scope() as session:
        job_rows = session.execute(
            select(Job.status, func.count(Job.id)).group_by(Job.status)
        ).all()
        project_rows = session.execute(
            select(Project.status, func.count(Project.id)).group_by(Project.status)
        ).all()

        oldest_pending = session.execute(
            select(func.min(Job.created_at)).where(Job.status == "pending")
        ).scalar()
        oldest_running = session.execute(
            select(func.min(Job.started_at)).where(Job.status == "running")
        ).scalar()

    def age_s(then: int | None) -> float | None:
        return None if then is None else max(0.0, (now_ms - then) / 1000.0)

    return StatsOut(
        jobs_by_status={s: c for s, c in job_rows},
        projects_by_status={s: c for s, c in project_rows},
        oldest_pending_age_s=age_s(oldest_pending),
        oldest_running_age_s=age_s(oldest_running),
    )
