"""Full-VOD scene cut detection for analyze-time scoring."""

from __future__ import annotations

import asyncio
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)


def _detect_all_cuts_sync(video_path: Path, *, threshold: float = 27.0) -> list[float]:
    try:
        from scenedetect import ContentDetector, SceneManager, open_video
    except ImportError:
        log.warning("scenedetect_not_installed")
        return []

    video = open_video(str(video_path))
    manager = SceneManager()
    manager.add_detector(ContentDetector(threshold=threshold))
    manager.detect_scenes(video=video)
    scene_list = manager.get_scene_list()
    return [start.get_seconds() for start, _end in scene_list]


async def detect_scene_cuts_full(
    video_path: Path,
    *,
    threshold: float = 27.0,
) -> list[float]:
    """Return absolute seconds where new scenes begin across the full video."""
    if not video_path.exists():
        return []
    return await asyncio.to_thread(
        _detect_all_cuts_sync, video_path, threshold=threshold
    )


def nearest_cut_distance(seconds: float, cuts: list[float], *, window: float = 3.0) -> float | None:
    """Distance to nearest scene cut within window, or None."""
    if not cuts:
        return None
    best = min(cuts, key=lambda c: abs(c - seconds))
    dist = abs(best - seconds)
    return dist if dist <= window else None
