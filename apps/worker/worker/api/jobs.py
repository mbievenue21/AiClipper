"""GET /jobs, POST /jobs, GET /jobs/{id} — minimal job API for the Next.js side."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from ..db import session_scope
from ..jobs import handlers, queue
from ..models import Job

router = APIRouter(prefix="/jobs", tags=["jobs"])


class EnqueueJobBody(BaseModel):
    type: str = Field(..., description="Handler type: ingest|transcribe|analyze|render|publish")
    payload: dict[str, Any] = Field(default_factory=dict)
    project_id: str | None = None
    depends_on: str | None = None
    max_attempts: int = 3


class JobOut(BaseModel):
    id: str
    type: str
    status: str
    progress: float
    progress_message: str | None
    attempts: int
    project_id: str | None
    created_at: int
    started_at: int | None
    finished_at: int | None
    error_message: str | None
    result: dict[str, Any] | None

    @classmethod
    def from_model(cls, job: Job) -> "JobOut":
        return cls(
            id=job.id,
            type=job.type,
            status=job.status,
            progress=job.progress,
            progress_message=job.progress_message,
            attempts=job.attempts,
            project_id=job.project_id,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            error_message=job.error_message,
            result=job.result,
        )


@router.post("", response_model=JobOut, status_code=201)
def enqueue_job(body: EnqueueJobBody) -> JobOut:
    if handlers.get_handler(body.type) is None:
        raise HTTPException(
            status_code=400,
            detail=f"No handler registered for job type {body.type!r}. "
            f"Available: {handlers.registered_types()}",
        )
    job = queue.enqueue(
        body.type,
        body.payload,
        project_id=body.project_id,
        depends_on=body.depends_on,
        max_attempts=body.max_attempts,
    )
    return JobOut.from_model(job)


@router.get("", response_model=list[JobOut])
def list_jobs(
    project_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[JobOut]:
    with session_scope() as session:
        stmt = select(Job).order_by(desc(Job.created_at)).limit(limit)
        if project_id is not None:
            stmt = stmt.where(Job.project_id == project_id)
        if status is not None:
            stmt = stmt.where(Job.status == status)
        rows = session.execute(stmt).scalars().all()
        return [JobOut.from_model(j) for j in rows]


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: str) -> JobOut:
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return JobOut.from_model(job)
