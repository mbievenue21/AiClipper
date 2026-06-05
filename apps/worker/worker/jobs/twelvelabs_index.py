"""TwelveLabs index job — upload/register source video (chunked when >2GB)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select

from ..config import get_settings
from ..db import session_scope
from ..media.probe import probe_video
from ..media.twelvelabs_split import chunk_output_dir, materialize_upload_chunks
from ..models import ExternalVideoIndex, Video
from ..providers.twelvelabs_assets import UploadCancelled
from ..providers.twelvelabs_client import TwelveLabsClient
from ..providers.twelvelabs_upload_plan import plan_upload_chunks
from . import queue
from .cancellation import JobCancelled, make_should_stop
from .handlers import ProgressReporter, register
from .pipeline_enqueue import forward_payload

log = structlog.get_logger(__name__)


@register("twelvelabs_index")
async def handle_twelvelabs_index(job, progress: ProgressReporter) -> dict[str, Any]:
    payload = job.payload
    project_id = payload.get("project_id") or job.project_id
    video_id = payload.get("video_id")
    if not project_id or not video_id:
        raise ValueError("twelvelabs_index requires project_id and video_id")

    settings = get_settings()
    client = TwelveLabsClient(settings)
    # Predicate that returns True when this job has been cancelled in the DB
    # (project deleted, re-analyze, manual cancel) or the worker is shutting
    # down. Threaded into the TwelveLabs upload loop so the worker stops
    # PUTing parts within ~1 part instead of running the multipart upload to
    # completion after Ctrl+C.
    should_stop = make_should_stop(job.id)

    if not client.enabled():
        log.info("twelvelabs_index_skipped_disabled", project_id=project_id)
        next_job = queue.enqueue(
            "analyze",
            forward_payload(payload, project_id=project_id, video_id=video_id),
            project_id=project_id,
        )
        return {"skipped": True, "reason": "disabled", "next_job_id": next_job.id}

    if not settings.twelvelabs_api_key:
        log.warning("twelvelabs_index_skipped_no_api_key", project_id=project_id)
        next_job = queue.enqueue(
            "analyze",
            forward_payload(payload, project_id=project_id, video_id=video_id),
            project_id=project_id,
        )
        return {"skipped": True, "reason": "missing_api_key", "next_job_id": next_job.id}

    if not settings.twelvelabs_index_id:
        log.warning("twelvelabs_index_skipped_no_index_id", project_id=project_id)
        next_job = queue.enqueue(
            "analyze",
            forward_payload(payload, project_id=project_id, video_id=video_id),
            project_id=project_id,
        )
        return {"skipped": True, "reason": "missing_index_id", "next_job_id": next_job.id}

    with session_scope() as session:
        video = session.get(Video, video_id)
        if video is None:
            raise ValueError(f"Video {video_id} not found")
        video_rel = video.file_path
        duration = float(video.duration_seconds or 0.0)

        existing_rows = (
            session.execute(
                select(ExternalVideoIndex)
                .where(
                    ExternalVideoIndex.project_id == project_id,
                    ExternalVideoIndex.video_id == video_id,
                    ExternalVideoIndex.provider == "twelvelabs",
                    ExternalVideoIndex.status == "ready",
                )
                .order_by(ExternalVideoIndex.chunk_index)
            )
            .scalars()
            .all()
        )

    media_root = settings.media_root_path
    video_abs = (media_root / video_rel).resolve()
    if not video_abs.exists():
        raise FileNotFoundError(f"Source video missing: {video_abs}")

    sha = TwelveLabsClient.file_sha256(video_abs)
    probe = await asyncio.to_thread(probe_video, video_abs)
    file_size = probe.size_bytes
    duration = float(probe.duration_seconds or duration or 0.0)

    upload_plans = plan_upload_chunks(
        file_size,
        duration,
        max_upload_bytes=settings.twelvelabs_max_upload_bytes,
        max_chunk_seconds=float(settings.twelvelabs_max_analyze_chunk_seconds),
        overlap_seconds=float(settings.twelvelabs_chunk_overlap_seconds),
    )

    if (
        existing_rows
        and settings.twelvelabs_reuse_existing_index
        and existing_rows[0].source_sha256 == sha
        and all(r.provider_video_id and r.provider_task_id for r in existing_rows)
        and _index_coverage_complete(existing_rows, upload_plans, duration)
    ):
        log.info(
            "twelvelabs_index_reuse",
            project_id=project_id,
            chunk_count=len(existing_rows),
        )
        next_job = queue.enqueue(
            "twelvelabs_analyze",
            forward_payload(payload, project_id=project_id, video_id=video_id),
            project_id=project_id,
        )
        return {
            "skipped": True,
            "reason": "reused",
            "chunk_count": len(existing_rows),
            "next_job_id": next_job.id,
        }

    log.info(
        "twelvelabs_upload_plan",
        project_id=project_id,
        file_size_gb=round(file_size / (1024**3), 3),
        duration_s=round(duration, 1),
        planned_chunks=len(upload_plans),
    )

    progress(0.05, f"splitting video into {len(upload_plans)} upload chunk(s)")
    project_dir = video_abs.parent
    chunk_dir = chunk_output_dir(project_dir)

    try:
        chunk_files = await asyncio.to_thread(
            materialize_upload_chunks,
            video_abs,
            upload_plans,
            chunk_dir,
            max_upload_bytes=settings.twelvelabs_max_upload_bytes,
        )
    except Exception as exc:
        log.exception("twelvelabs_split_failed", project_id=project_id)
        _persist_index_failure(project_id, video_id, video_rel, sha, duration, str(exc))
        if settings.twelvelabs_fail_open:
            next_job = queue.enqueue(
                "analyze",
                forward_payload(payload, project_id=project_id, video_id=video_id),
                project_id=project_id,
            )
            return {"failed_open": True, "error": str(exc)[:500], "next_job_id": next_job.id}
        raise

    uploaded: list[dict[str, Any]] = []
    total = len(chunk_files)

    try:
        for i, (upload_idx, plan, chunk_path) in enumerate(chunk_files):
            frac = 0.1 + 0.85 * (i / max(total, 1))
            progress(
                frac,
                f"TwelveLabs upload {i + 1}/{total} "
                f"({plan.start_seconds:.0f}s–{plan.end_seconds:.0f}s, "
                f"{chunk_path.stat().st_size / (1024**2):.0f} MB)",
            )
            chunk_sha = TwelveLabsClient.file_sha256(chunk_path)
            result = await asyncio.to_thread(
                _run_index_upload,
                client,
                project_id=project_id,
                video_abs=chunk_path,
                sha=chunk_sha,
                chunk_index=upload_idx,
                chunk_start=plan.start_seconds,
                chunk_end=plan.end_seconds,
                should_stop=should_stop,
            )
            uploaded.append(
                {
                    "chunk_index": upload_idx,
                    "chunk_start_seconds": plan.start_seconds,
                    "chunk_end_seconds": plan.end_seconds,
                    "chunk_path": str(chunk_path.relative_to(media_root)),
                    "chunk_size_bytes": chunk_path.stat().st_size,
                    "provider_video_id": result.provider_video_id,
                    "provider_task_id": result.provider_task_id,
                    "metadata": result.metadata,
                }
            )
    except UploadCancelled as exc:
        # Cooperative shutdown from inside the upload loop. Don't mark the
        # job failed (that would retry forever) — surface as JobCancelled so
        # the runner records status='cancelled'.
        log.info("twelvelabs_index_cancelled", project_id=project_id, reason=str(exc))
        _persist_partial_uploads(
            project_id, video_id, video_rel, sha, duration, uploaded, f"cancelled: {exc}"
        )
        raise JobCancelled(f"twelvelabs upload cancelled: {exc}") from exc
    except JobCancelled:
        _persist_partial_uploads(
            project_id, video_id, video_rel, sha, duration, uploaded, "cancelled"
        )
        raise
    except Exception as exc:
        log.exception("twelvelabs_index_failed", project_id=project_id)
        _persist_partial_uploads(project_id, video_id, video_rel, sha, duration, uploaded, str(exc))
        if settings.twelvelabs_fail_open:
            next_job = queue.enqueue(
                "analyze",
                forward_payload(payload, project_id=project_id, video_id=video_id),
                project_id=project_id,
            )
            return {
                "failed_open": True,
                "error": str(exc)[:500],
                "chunks_uploaded": len(uploaded),
                "next_job_id": next_job.id,
            }
        raise

    progress(0.95, "saving TwelveLabs index state")
    index_ids: list[str] = []
    with session_scope() as session:
        for item in uploaded:
            row = ExternalVideoIndex(
                project_id=project_id,
                video_id=video_id,
                provider="twelvelabs",
                provider_index_id=settings.twelvelabs_index_id,
                provider_video_id=item["provider_video_id"],
                provider_task_id=item["provider_task_id"],
                status="ready",
                source_path=item["chunk_path"],
                source_sha256=sha,
                duration_seconds=duration,
                chunk_index=int(item["chunk_index"]),
                chunk_start_seconds=float(item["chunk_start_seconds"]),
                chunk_end_seconds=float(item["chunk_end_seconds"]),
                metadata_json=json.dumps(item.get("metadata") or {}),
            )
            session.add(row)
            session.flush()
            index_ids.append(row.id)

    next_job = queue.enqueue(
        "twelvelabs_analyze",
        {"project_id": project_id, "video_id": video_id},
        project_id=project_id,
    )
    progress(1.0, f"TwelveLabs index ready ({total} chunk(s))")
    log.info(
        "twelvelabs_index_done",
        project_id=project_id,
        chunk_count=total,
        file_size_gb=round(file_size / (1024**3), 3),
    )
    return {
        "chunk_count": total,
        "external_index_ids": index_ids,
        "status": "ready",
        "next_job_id": next_job.id,
    }


def _index_coverage_complete(
    existing_rows: list[ExternalVideoIndex],
    upload_plans: list,
    duration: float,
) -> bool:
    """True when stored index rows cover the full planned upload (no partial runs)."""
    if len(existing_rows) < len(upload_plans):
        return False
    max_end = max(float(r.chunk_end_seconds or 0.0) for r in existing_rows)
    planned_end = max(p.end_seconds for p in upload_plans) if upload_plans else duration
    target_end = max(duration, planned_end)
    return max_end >= target_end - 5.0


def _run_index_upload(
    client: TwelveLabsClient,
    *,
    project_id: str,
    video_abs: Path,
    sha: str,
    chunk_index: int,
    chunk_start: float,
    chunk_end: float,
    should_stop=None,
):
    result = client.ensure_index(
        project_id=project_id,
        video_path=video_abs,
        source_sha256=sha,
        chunk_index=chunk_index,
        chunk_start_seconds=chunk_start,
        chunk_end_seconds=chunk_end,
        should_stop=should_stop,
    )
    if not result.provider_video_id or not result.provider_task_id:
        raise RuntimeError("TwelveLabs asset/index upload did not return ids")
    return result


def _persist_index_failure(
    project_id: str,
    video_id: str,
    video_rel: str,
    sha: str,
    duration: float,
    error: str,
) -> None:
    with session_scope() as session:
        row = ExternalVideoIndex(
            project_id=project_id,
            video_id=video_id,
            provider="twelvelabs",
            status="failed",
            source_path=video_rel,
            source_sha256=sha,
            duration_seconds=duration,
            error_message=error[:2000],
        )
        session.add(row)


def _persist_partial_uploads(
    project_id: str,
    video_id: str,
    video_rel: str,
    sha: str,
    duration: float,
    uploaded: list[dict[str, Any]],
    error: str,
) -> None:
    with session_scope() as session:
        for item in uploaded:
            row = ExternalVideoIndex(
                project_id=project_id,
                video_id=video_id,
                provider="twelvelabs",
                provider_video_id=item.get("provider_video_id"),
                provider_task_id=item.get("provider_task_id"),
                status="ready",
                source_path=item.get("chunk_path") or video_rel,
                source_sha256=sha,
                duration_seconds=duration,
                chunk_index=int(item["chunk_index"]),
                chunk_start_seconds=float(item["chunk_start_seconds"]),
                chunk_end_seconds=float(item["chunk_end_seconds"]),
            )
            session.add(row)
        fail_row = ExternalVideoIndex(
            project_id=project_id,
            video_id=video_id,
            provider="twelvelabs",
            status="failed",
            source_path=video_rel,
            source_sha256=sha,
            duration_seconds=duration,
            error_message=error[:2000],
        )
        session.add(fail_row)
