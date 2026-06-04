"""Background job loop.

Started by FastAPI's lifespan (see worker.main). Owns a single asyncio
task that polls for `pending` jobs and dispatches them to handlers
registered via worker.jobs.handlers.register.

Concurrency is bounded by `Settings.job_max_concurrent` (default 1).
For most personal-use workloads 1 is right: transcription and ffmpeg
already saturate the GPU/CPU.
"""

from __future__ import annotations

import asyncio
import contextlib
import traceback

import structlog

from ..config import get_settings
from ..logging import get_logger
from . import handlers, queue

log = get_logger(__name__)


class JobRunner:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        settings = get_settings()
        self._sem = asyncio.Semaphore(settings.job_max_concurrent)
        self._poll = settings.job_poll_interval_seconds

    async def start(self) -> None:
        if self._task is not None:
            return
        # If a previous worker died mid-job (e.g. uvicorn --reload killed the
        # child while it was running), the row is stuck as "running". Reset
        # before we start polling so the job retries naturally.
        try:
            reset = queue.reset_stuck_running_jobs()
            if reset:
                log.info("reset_stuck_running_jobs", count=reset)
        except Exception:
            log.exception("reset_stuck_running_jobs_failed")
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop(), name="job-runner")
        log.info(
            "job_runner_started",
            poll_interval_s=self._poll,
            registered=handlers.registered_types(),
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        log.info("job_runner_stopped")

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                job = queue.claim_next()
            except Exception:
                log.exception("job_claim_failed")
                await asyncio.sleep(self._poll)
                continue

            if job is None:
                await asyncio.sleep(self._poll)
                continue

            asyncio.create_task(self._run_one(job.id, job.type))

    async def _run_one(self, job_id: str, job_type: str) -> None:
        async with self._sem:
            handler = handlers.get_handler(job_type)
            bound_log = log.bind(job_id=job_id, job_type=job_type)

            if handler is None:
                bound_log.warning("no_handler_registered")
                queue.mark_failed(
                    job_id,
                    f"No handler registered for job type {job_type!r}",
                    retryable=False,
                )
                return

            def report(progress: float, message: str | None = None) -> None:
                queue.update_progress(job_id, progress, message)

            structlog.contextvars.bind_contextvars(job_id=job_id, job_type=job_type)
            try:
                # Re-fetch the job inside the handler scope so handlers get a
                # fresh, attached ORM object.
                from ..db import session_scope
                from ..models import Job as JobModel

                with session_scope() as session:
                    job_obj = session.get(JobModel, job_id)
                    if job_obj is None:
                        bound_log.warning("job_disappeared")
                        return
                    session.expunge(job_obj)

                result = await handler(job_obj, report)
                queue.mark_succeeded(job_id, result)
                bound_log.info("job_succeeded")
            except Exception as exc:
                bound_log.exception("job_failed")
                queue.mark_failed(job_id, f"{exc}\n{traceback.format_exc()}")
            finally:
                structlog.contextvars.unbind_contextvars("job_id", "job_type")


runner = JobRunner()
