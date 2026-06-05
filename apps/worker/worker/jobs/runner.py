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
from ..pipeline_timing import complete_run, record_job_timing
from . import cancellation, handlers, queue

log = get_logger(__name__)

# How long we wait for in-flight handler tasks to honor cooperative cancellation
# before letting the asyncio loop close out from under them. Tuned to be long
# enough for a TwelveLabs PUT round-trip but short enough that Ctrl+C feels
# responsive.
_GRACEFUL_SHUTDOWN_SECONDS = 12.0


class JobRunner:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        # Track every in-flight handler task so stop() can cancel them all
        # and wait briefly for them to bail. Otherwise long synchronous loops
        # inside asyncio.to_thread keep spamming external APIs after uvicorn
        # has already logged "Application shutdown complete".
        self._inflight: set[asyncio.Task] = set()
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

        # Same idea for projects: if we crashed between INSERT project and
        # POST /jobs, the project row sits in pending/ingesting/etc. forever
        # with no active job. Mark those as failed so the UI shows the
        # truth and the user can move on.
        try:
            healed = queue.heal_orphan_projects()
            if healed:
                log.info("heal_orphan_projects", count=healed)
        except Exception:
            log.exception("heal_orphan_projects_failed")
        self._stop_event.clear()
        # Publish the shutdown signal to the cancellation module so any
        # checkpoint inside a long-running handler can react to Ctrl+C.
        cancellation.set_shutdown_event(self._stop_event)
        self._task = asyncio.create_task(self._loop(), name="job-runner")
        log.info(
            "job_runner_started",
            poll_interval_s=self._poll,
            registered=handlers.registered_types(),
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        # 1) Tell everyone (poll loop + in-flight handlers) we're shutting down.
        self._stop_event.set()
        # 2) Cancel the polling task so it doesn't claim a fresh job mid-shutdown.
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        # 3) Cooperatively wait for in-flight handlers to notice the stop
        # signal (via update_progress -> check_cancelled) and unwind. We
        # also cancel them so any awaits get an asyncio.CancelledError; the
        # synchronous thread bodies (TwelveLabs uploads, ffmpeg, yt-dlp)
        # will exit at their next cancellation checkpoint.
        if self._inflight:
            log.info("job_runner_draining", inflight=len(self._inflight))
            for t in list(self._inflight):
                t.cancel()
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._inflight, return_exceptions=True),
                    timeout=_GRACEFUL_SHUTDOWN_SECONDS,
                )
            except asyncio.TimeoutError:
                # Threads inside asyncio.to_thread can't be force-killed; we
                # log and let uvicorn proceed. The OS reaps them on exit.
                log.warning(
                    "job_runner_drain_timeout",
                    still_running=len(self._inflight),
                    seconds=_GRACEFUL_SHUTDOWN_SECONDS,
                )
        log.info("job_runner_stopped")

    async def _loop(self) -> None:
        # Tick the scheduler every N seconds so due uploads get enqueued
        # automatically. Cheap (one indexed query) so we run it frequently.
        last_scheduler_tick = 0.0
        scheduler_interval_s = 15.0

        while not self._stop_event.is_set():
            now = asyncio.get_event_loop().time()
            if now - last_scheduler_tick >= scheduler_interval_s:
                last_scheduler_tick = now
                try:
                    enqueued = queue.enqueue_due_uploads()
                    if enqueued:
                        log.info("scheduler_tick_enqueued", count=len(enqueued))
                except Exception:
                    log.exception("scheduler_tick_failed")

            try:
                job = queue.claim_next()
            except Exception:
                log.exception("job_claim_failed")
                await asyncio.sleep(self._poll)
                continue

            if job is None:
                await asyncio.sleep(self._poll)
                continue

            task = asyncio.create_task(
                self._run_one(job.id, job.type), name=f"job:{job.id}"
            )
            self._inflight.add(task)
            task.add_done_callback(self._inflight.discard)

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
                # update_progress also raises JobCancelled if the job row was
                # cancelled / deleted / the worker is shutting down. This is
                # how long-running handlers (TwelveLabs upload, yt-dlp,
                # ffmpeg) discover they should bail.
                queue.update_progress(job_id, progress, message)

            structlog.contextvars.bind_contextvars(job_id=job_id, job_type=job_type)
            job_obj = None
            result: dict | None = None
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
                if job_obj is not None:
                    payload = job_obj.payload
                    project_id = job_obj.project_id or payload.get("project_id")
                    skipped = bool(isinstance(result, dict) and result.get("skipped"))
                    record_job_timing(
                        job_id,
                        job_type,
                        str(project_id) if project_id else None,
                        payload,
                        skipped=skipped,
                        meta={"result_keys": list(result.keys())[:12]} if result else None,
                    )
                    run_id = payload.get("pipeline_run_id")
                    if job_type == "analyze" and run_id and not skipped:
                        complete_run(str(run_id), status="complete")
            except cancellation.JobCancelled as exc:
                # Graceful, expected unwind — don't retry, don't log a stack.
                bound_log.info("job_cancelled", reason=str(exc))
                queue.mark_cancelled(job_id, str(exc))
            except asyncio.CancelledError:
                # The runner cancelled us during shutdown. Same handling as
                # JobCancelled — don't mark failed (would retry forever).
                bound_log.info("job_cancelled", reason="asyncio cancel (shutdown)")
                queue.mark_cancelled(job_id, "worker shutdown")
                raise
            except Exception as exc:
                bound_log.exception("job_failed")
                err_text = f"{exc}\n{traceback.format_exc()}"
                queue.mark_failed(job_id, err_text)
                if job_obj is not None:
                    payload = job_obj.payload
                    project_id = job_obj.project_id or payload.get("project_id")
                    record_job_timing(
                        job_id,
                        job_type,
                        str(project_id) if project_id else None,
                        payload,
                        error=str(exc),
                    )
                    run_id = payload.get("pipeline_run_id")
                    if job_type == "analyze" and run_id:
                        complete_run(str(run_id), status="failed")
            finally:
                structlog.contextvars.unbind_contextvars("job_id", "job_type")


runner = JobRunner()
