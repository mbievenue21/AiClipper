"""Job handler registry.

Each pipeline step (see worker/jobs/ subfolders to be added in later steps)
registers a coroutine here that takes a Job and a ProgressReporter.

Example:

    from worker.jobs.handlers import register

    @register("ingest")
    async def handle_ingest(job: Job, progress: ProgressReporter) -> dict:
        ...
        return {"video_id": "abc"}
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from ..models import Job

ProgressReporter = Callable[[float, str | None], None]
JobHandler = Callable[[Job, ProgressReporter], Awaitable[dict[str, Any] | None]]

_registry: dict[str, JobHandler] = {}


def register(job_type: str) -> Callable[[JobHandler], JobHandler]:
    def decorator(fn: JobHandler) -> JobHandler:
        if job_type in _registry:
            raise ValueError(f"Duplicate handler for job type {job_type!r}")
        _registry[job_type] = fn
        return fn

    return decorator


def get_handler(job_type: str) -> JobHandler | None:
    return _registry.get(job_type)


def registered_types() -> list[str]:
    return sorted(_registry.keys())
