"""Persist per-stage pipeline timings for bottleneck analytics."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator

import structlog

from .config import get_settings
from .db import session_scope
from .models import Job, PipelineRun, PipelineStageTiming

log = structlog.get_logger(__name__)

JOB_STAGE_MAP: dict[str, str] = {
    "ingest": "ingest",
    "transcribe": "transcribe",
    "twelvelabs_index": "twelvelabs_index",
    "twelvelabs_analyze": "twelvelabs_visual",
}

STAGE_KEYS = frozenset(
    {
        "ingest",
        "transcribe",
        "twelvelabs_index",
        "twelvelabs_visual",
        "librosa_audio",
        "chat_density",
        "candidate_generation",
        "candidate_fusion",
        "gemini_rerank",
        "highlights_build",
        "highlights_save",
    }
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def begin_run(
    project_id: str,
    *,
    video_duration_seconds: float | None = None,
    twelvelabs_enabled: bool = False,
    is_reanalysis: bool = False,
    meta: dict[str, Any] | None = None,
) -> str:
    """Start a new pipeline run (full ingest or partial re-analysis)."""
    with session_scope() as session:
        row = PipelineRun(
            project_id=project_id,
            status="running",
            video_duration_seconds=video_duration_seconds,
            twelvelabs_enabled=twelvelabs_enabled,
            is_reanalysis=is_reanalysis,
        )
        if meta:
            row.meta = meta
        session.add(row)
        session.flush()
        run_id = row.id
    log.info(
        "pipeline_run_started",
        project_id=project_id,
        run_id=run_id,
        reanalysis=is_reanalysis,
    )
    return run_id


def ensure_run_id(
    project_id: str,
    payload: dict[str, Any],
    *,
    partial: bool = False,
) -> str:
    """Return pipeline_run_id from payload, or create a partial run if missing."""
    existing = payload.get("pipeline_run_id")
    if existing:
        return str(existing)
    settings = get_settings()
    return begin_run(
        project_id,
        twelvelabs_enabled=bool(settings.twelvelabs_enabled),
        is_reanalysis=partial,
        meta={"source": "ensure_run_id"},
    )


def update_run(
    run_id: str,
    *,
    video_duration_seconds: float | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    with session_scope() as session:
        row = session.get(PipelineRun, run_id)
        if row is None:
            return
        if video_duration_seconds is not None:
            row.video_duration_seconds = video_duration_seconds
        if meta:
            combined = {**row.meta, **meta}
            row.meta = combined


def complete_run(run_id: str, *, status: str = "complete") -> None:
    with session_scope() as session:
        row = session.get(PipelineRun, run_id)
        if row is None:
            return
        row.status = status
        row.finished_at = _now_ms()


def record_stage(
    *,
    run_id: str,
    project_id: str,
    stage: str,
    duration_ms: int,
    started_at: int | None = None,
    finished_at: int | None = None,
    status: str = "ok",
    job_id: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    if stage not in STAGE_KEYS:
        log.warning("pipeline_unknown_stage", stage=stage)
    finished = finished_at or _now_ms()
    started = started_at if started_at is not None else finished - max(0, duration_ms)
    with session_scope() as session:
        session.add(
            PipelineStageTiming(
                run_id=run_id,
                project_id=project_id,
                stage=stage,
                duration_ms=max(0, int(duration_ms)),
                started_at=started,
                finished_at=finished,
                status=status,
                job_id=job_id,
                meta=meta,
            )
        )
    log.info(
        "pipeline_stage_recorded",
        project_id=project_id,
        run_id=run_id,
        stage=stage,
        duration_ms=duration_ms,
        status=status,
    )


def record_stages(
    *,
    run_id: str,
    project_id: str,
    stages: dict[str, int],
    status: str = "ok",
    job_id: str | None = None,
) -> None:
    """Bulk-insert analyze sub-stage timings (values are duration_ms)."""
    finished = _now_ms()
    cursor = finished
    # Insert in reverse order so started_at chain is approximate but monotonic.
    items = [(k, v) for k, v in stages.items() if k in STAGE_KEYS and v >= 0]
    for stage, duration_ms in reversed(items):
        started = cursor - duration_ms
        record_stage(
            run_id=run_id,
            project_id=project_id,
            stage=stage,
            duration_ms=duration_ms,
            started_at=started,
            finished_at=cursor,
            status=status,
            job_id=job_id,
        )
        cursor = started


def _infer_status_from_error(error: str | None) -> str:
    if not error:
        return "failed"
    low = error.lower()
    if "timed out" in low or "timeout" in low:
        return "timeout"
    return "failed"


def record_job_timing(
    job_id: str,
    job_type: str,
    project_id: str | None,
    payload: dict[str, Any],
    *,
    error: str | None = None,
    skipped: bool = False,
    meta: dict[str, Any] | None = None,
) -> None:
    """Record wall-clock timing for a completed job row."""
    stage = JOB_STAGE_MAP.get(job_type)
    if not stage or not project_id:
        return
    run_id = payload.get("pipeline_run_id")
    if not run_id:
        return

    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None or not job.started_at:
            return
        started_at = int(job.started_at)
        finished_at = int(job.finished_at or _now_ms())
        duration_ms = max(0, finished_at - started_at)

    status = "skipped" if skipped else ("ok" if not error else _infer_status_from_error(error))
    combined_meta = dict(meta or {})
    if error:
        combined_meta["error"] = error[:500]

    record_stage(
        run_id=str(run_id),
        project_id=project_id,
        stage=stage,
        duration_ms=duration_ms,
        started_at=started_at,
        finished_at=finished_at,
        status=status,
        job_id=job_id,
        meta=combined_meta or None,
    )


@contextmanager
def stage_timer(
    *,
    run_id: str,
    project_id: str,
    stage: str,
    job_id: str | None = None,
    meta: dict[str, Any] | None = None,
) -> Iterator[None]:
    """Context manager for synchronous sub-stages inside analyze."""
    started = time.perf_counter()
    started_at = _now_ms()
    err: str | None = None
    status = "ok"
    try:
        yield
    except Exception as exc:
        err = str(exc)
        status = _infer_status_from_error(err)
        raise
    finally:
        duration_ms = int((time.perf_counter() - started) * 1000)
        stage_meta = dict(meta or {})
        if err:
            stage_meta["error"] = err[:500]
        record_stage(
            run_id=run_id,
            project_id=project_id,
            stage=stage,
            duration_ms=duration_ms,
            started_at=started_at,
            finished_at=_now_ms(),
            status=status,
            job_id=job_id,
            meta=stage_meta or None,
        )
