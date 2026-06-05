"""Cooperative cancellation for long-running jobs.

Why this exists:

The job runner runs handlers on the asyncio loop, but the heavy lifting
(yt-dlp, ffmpeg, librosa, TwelveLabs multipart uploads) happens in worker
threads via ``asyncio.to_thread``. Python cannot interrupt a thread, so
without explicit cancellation checkpoints those threads keep running even
after:

  * the user hits Ctrl+C and uvicorn finishes shutting down,
  * the user deletes the project from the UI (which cancels the job row),
  * a re-analyze wipes pending work,
  * the worker is asked to reset.

A correctly written long-running job calls ``check_cancelled(job_id)`` at
each natural checkpoint (loop iteration, between API calls, after every
disk chunk). If the job no longer exists or has been marked cancelled, or
if the worker is shutting down, this raises :class:`JobCancelled` and the
handler unwinds cleanly. The runner treats ``JobCancelled`` as a graceful
cancel (status='cancelled'), not a failure.

``update_progress`` calls ``check_cancelled`` as a side effect, so every
handler that reports progress gets cancellation for free.
"""

from __future__ import annotations

import asyncio
from typing import Callable

from sqlalchemy import select

from ..db import session_scope
from ..models import Job


class JobCancelled(Exception):
    """Raised when a job should stop because it was cancelled or its project was deleted."""


# Set by JobRunner.start() so cancellation checkpoints can react immediately
# to worker shutdown (Ctrl+C). asyncio.Event is thread-safe to read via
# .is_set(), which is all we need.
_shutdown_event: asyncio.Event | None = None


def set_shutdown_event(event: asyncio.Event) -> None:
    global _shutdown_event
    _shutdown_event = event


def is_shutting_down() -> bool:
    return _shutdown_event is not None and _shutdown_event.is_set()


def check_cancelled(job_id: str) -> None:
    """Raise :class:`JobCancelled` if the worker is shutting down or the job
    row was cancelled / deleted out from under us.

    Cheap: one indexed SELECT against the jobs table. Safe to call from
    inside worker threads since SQLAlchemy's session_scope opens its own
    short-lived connection.
    """
    if is_shutting_down():
        raise JobCancelled("worker is shutting down")

    with session_scope() as session:
        status = session.execute(
            select(Job.status).where(Job.id == job_id)
        ).scalar_one_or_none()

    if status is None:
        # Job row gone — almost always means the project was deleted while
        # the handler was mid-flight (FK cascade wipes the job rows).
        raise JobCancelled(f"job {job_id} no longer exists (project deleted?)")
    if status == "cancelled":
        raise JobCancelled(f"job {job_id} cancelled in DB")


def make_should_stop(job_id: str) -> Callable[[], bool]:
    """Return a thread-safe ``() -> bool`` suitable for passing into
    synchronous code (e.g. the TwelveLabs upload loop) that runs in a
    worker thread. Returns True when the caller should bail out.
    """

    def _check() -> bool:
        try:
            check_cancelled(job_id)
            return False
        except JobCancelled:
            return True

    return _check
