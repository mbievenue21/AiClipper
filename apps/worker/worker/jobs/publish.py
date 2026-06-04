"""Publish job: upload a single clip to one platform.

There are two ways a publish job gets created:

1. *Post now*: the web app creates a ``scheduled_uploads`` row with
   ``scheduled_for = now`` AND enqueues a publish job pointing at it.
2. *Schedule for later*: the web app only creates the ``scheduled_uploads``
   row. The worker's scheduler tick (see ``runner.py``) scans the table
   periodically and enqueues publish jobs for any row whose scheduled time
   has passed and that's still ``pending``.

Either path lands here. The handler is idempotent — re-running it on an
already-uploaded row is a no-op.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import structlog

from ..config import get_settings
from ..db import session_scope
from ..models import Account, Clip, ScheduledUpload
from ..publish import get_uploader
from ..publish.errors import AuthExpiredError, PublishError
from .handlers import ProgressReporter, register

log = structlog.get_logger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _media_abs(rel_path_str: str) -> Path:
    return (get_settings().media_root_path / rel_path_str).resolve()


@register("publish")
async def handle_publish(job, progress: ProgressReporter) -> dict[str, Any]:
    payload = job.payload
    upload_id = payload.get("upload_id")
    if not upload_id:
        raise ValueError("publish job requires upload_id in payload")

    log.info("publish_start", upload_id=upload_id)

    progress(0.05, "loading upload + account")
    with session_scope() as session:
        upload = session.get(ScheduledUpload, upload_id)
        if upload is None:
            raise ValueError(f"ScheduledUpload {upload_id!r} not found")
        if upload.status == "uploaded":
            log.info("publish_already_done", external_id=upload.external_id)
            return {
                "upload_id": upload_id,
                "external_id": upload.external_id,
                "external_url": upload.external_url,
                "noop": True,
            }
        if upload.status == "cancelled":
            log.info("publish_cancelled")
            return {"upload_id": upload_id, "noop": True, "cancelled": True}

        if upload.scheduled_for > _now_ms() + 5_000:
            # Safety: never publish more than 5s before scheduled time.
            raise RuntimeError(
                f"Upload {upload_id} is scheduled for the future; refusing to publish early."
            )

        clip = session.get(Clip, upload.clip_id)
        account = session.get(Account, upload.account_id)
        if clip is None or account is None:
            raise ValueError("Upload references missing clip or account")
        if clip.status != "ready":
            raise ValueError(f"Clip {clip.id} not ready (status={clip.status!r})")

        # Prefer captioned file if it exists; otherwise the clean clip.
        rel_video = clip.captioned_file_path or clip.file_path
        title = upload.title
        description = upload.description
        tags = upload.tags
        visibility = upload.visibility
        platform = upload.platform
        access_token = account.access_token
        refresh_token = account.refresh_token

        upload.status = "uploading"
        upload.attempts = (upload.attempts or 0) + 1
        upload.updated_at = _now_ms()
        upload.error_message = None

    video_abs = _media_abs(rel_video)
    if not video_abs.exists():
        raise FileNotFoundError(f"Clip file missing on disk: {video_abs}")

    progress(0.30, f"uploading to {platform}")
    uploader = get_uploader(platform)
    try:
        result = await uploader.upload(
            access_token=access_token,
            refresh_token=refresh_token,
            video_path=str(video_abs),
            title=title,
            description=description,
            tags=tags or [],
            visibility=visibility,
        )
    except AuthExpiredError as exc:
        with session_scope() as session:
            upload = session.get(ScheduledUpload, upload_id)
            if upload is not None:
                upload.status = "failed"
                upload.error_message = f"Reconnect your {platform} account: {exc}"[:2000]
                upload.updated_at = _now_ms()
        raise
    except PublishError as exc:
        with session_scope() as session:
            upload = session.get(ScheduledUpload, upload_id)
            if upload is not None:
                upload.status = "failed"
                upload.error_message = str(exc)[:2000]
                upload.updated_at = _now_ms()
        raise

    progress(0.95, "saving upload result")
    with session_scope() as session:
        upload = session.get(ScheduledUpload, upload_id)
        if upload is None:
            raise RuntimeError(f"Upload {upload_id} disappeared mid-publish")
        upload.status = "uploaded"
        upload.external_id = result.external_id
        upload.external_url = result.external_url
        upload.error_message = None
        upload.updated_at = _now_ms()

    progress(1.0, "published")
    log.info(
        "publish_done",
        upload_id=upload_id,
        platform=platform,
        external_id=result.external_id,
        external_url=result.external_url,
    )
    return {
        "upload_id": upload_id,
        "platform": platform,
        "external_id": result.external_id,
        "external_url": result.external_url,
    }
