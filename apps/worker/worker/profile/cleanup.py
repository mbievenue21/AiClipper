"""Free training media after profile optimization completes."""

from __future__ import annotations

import shutil
from pathlib import Path

import structlog
from sqlalchemy import delete, select

from ..config import get_settings
from ..db import session_scope
from ..models import ExtractedFeatureWindow, ReferenceClip, TrainingDataset

log = structlog.get_logger(__name__)


def cleanup_dataset_media(dataset_id: str, *, keep_examples: bool = True) -> int:
    """Remove reference clip files and feature windows; retain training examples."""
    media_root = get_settings().media_root_path
    removed = 0

    with session_scope() as session:
        dataset = session.get(TrainingDataset, dataset_id)
        if dataset is None:
            return 0

        clips = (
            session.execute(
                select(ReferenceClip).where(ReferenceClip.dataset_id == dataset_id)
            )
            .scalars()
            .all()
        )

        for clip in clips:
            if clip.file_path:
                abs_path = (media_root / clip.file_path).resolve()
                if abs_path.exists():
                    try:
                        abs_path.unlink()
                        removed += 1
                    except OSError:
                        log.warning("cleanup_file_failed", path=str(abs_path))

        clip_ids = [c.id for c in clips]
        if clip_ids:
            session.execute(
                delete(ExtractedFeatureWindow).where(
                    ExtractedFeatureWindow.reference_clip_id.in_(clip_ids)
                )
            )

        dataset_dir = media_root / "profiles" / dataset.profile_id / dataset_id
        for subdir in ("uploads", "downloads"):
            path = dataset_dir / subdir
            if path.exists():
                try:
                    shutil.rmtree(path, ignore_errors=True)
                except OSError:
                    log.warning("cleanup_dir_failed", path=str(path))

    log.info("training_cleanup_done", dataset_id=dataset_id, files_removed=removed)
    return removed
