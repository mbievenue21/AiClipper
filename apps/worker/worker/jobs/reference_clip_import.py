"""Import reference clips for profile training."""

from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path
from typing import Any

import structlog

from ..config import get_settings
from ..db import session_scope
from ..ids import new_id
from ..media.paths import rel_path
from ..media.probe import probe_video
from ..media.source_url import detect_source_type
from ..models import ReferenceClip, TrainingDataset
from .handlers import ProgressReporter, register
from .ingest import _require_binaries, _run_yt_dlp
from .pipeline_enqueue import forward_payload

log = structlog.get_logger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _profile_data_dir(profile_id: str, dataset_id: str) -> Path:
    root = get_settings().media_root_path / "profiles" / profile_id / dataset_id
    root.mkdir(parents=True, exist_ok=True)
    return root


def _resolve_upload_path(source_path: str) -> Path:
    src = Path(source_path)
    if not src.is_absolute():
        src = (get_settings().media_root_path / source_path).resolve()
    if not src.exists():
        raise FileNotFoundError(f"Source clip not found: {src}")
    return src


@register("reference_clip_import")
async def handle_reference_clip_import(
    job, progress: ProgressReporter
) -> dict[str, Any]:
    payload = job.payload
    dataset_id = payload.get("dataset_id")
    profile_id = payload.get("profile_id")
    source_path = payload.get("source_path")
    source_type = payload.get("source_type", "uploaded_clip")
    title = payload.get("title")
    source_url = payload.get("source_url")

    if not dataset_id or not profile_id:
        raise ValueError("reference_clip_import requires dataset_id and profile_id")
    if not source_path and not source_url:
        raise ValueError(
            "reference_clip_import requires source_path (upload) or source_url (download)"
        )

    dest_dir = _profile_data_dir(profile_id, dataset_id)
    clip_id = new_id()

    if source_url:
        _require_binaries()
        url = str(source_url).strip()
        try:
            source_type = detect_source_type(url)
        except ValueError:
            source_type = str(payload.get("source_type") or "youtube")

        progress(0.1, f"downloading reference clip ({source_type})")
        download_dir = dest_dir / "downloads" / clip_id
        downloaded = await _run_yt_dlp(url, download_dir, progress)
        dest = dest_dir / f"{clip_id}{downloaded.suffix or '.mp4'}"
        await asyncio.to_thread(shutil.move, downloaded, dest)
        shutil.rmtree(download_dir, ignore_errors=True)
    else:
        progress(0.2, "copying reference clip")
        src = _resolve_upload_path(str(source_path))
        dest = dest_dir / f"{clip_id}{src.suffix or '.mp4'}"
        await asyncio.to_thread(shutil.copy2, src, dest)

    meta = await asyncio.to_thread(probe_video, dest)
    duration = float(meta.duration_seconds or 0.0)

    with session_scope() as session:
        ds = session.get(TrainingDataset, dataset_id)
        if ds is None:
            raise ValueError(f"Dataset {dataset_id} not found")

        stored_path = rel_path(dest)
        row = ReferenceClip(
            id=clip_id,
            dataset_id=dataset_id,
            source_type=source_type,
            source_url=source_url,
            file_path=stored_path,
            title=title or (Path(str(source_url)).name if source_url else dest.stem),
            duration_seconds=duration,
        )
        session.add(row)
        ds.updated_at = _now_ms()

    from . import queue

    queue.enqueue(
        "reference_feature_extract",
        forward_payload(
            payload,
            reference_clip_id=clip_id,
            dataset_id=dataset_id,
            profile_id=profile_id,
            label=payload.get("label", "positive"),
        ),
    )

    progress(1.0, "reference clip imported")
    return {
        "reference_clip_id": clip_id,
        "duration_seconds": duration,
        "source_type": source_type,
        "source_url": source_url,
    }
