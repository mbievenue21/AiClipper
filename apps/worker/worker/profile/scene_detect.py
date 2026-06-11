"""Full-video scene cut detection for feature extraction."""

from __future__ import annotations

from pathlib import Path

import structlog

log = structlog.get_logger(__name__)


def detect_scene_cuts_full(
    video_path: Path,
    *,
    threshold: float = 27.0,
    max_duration_seconds: float | None = None,
) -> list[float]:
    """Return scene cut timestamps (seconds) for the full video or prefix."""
    if not video_path.exists():
        return []
    try:
        from scenedetect import ContentDetector, SceneManager, open_video
    except ImportError:
        log.warning("scenedetect_not_installed")
        return []

    video = open_video(str(video_path))
    fps = video.frame_rate or 30.0
    end_frame = None
    if max_duration_seconds is not None:
        end_frame = int(max_duration_seconds * fps)

    manager = SceneManager()
    manager.add_detector(ContentDetector(threshold=threshold))
    manager.detect_scenes(video=video, end_time=end_frame)
    scene_list = manager.get_scene_list()

    cuts: list[float] = []
    for start, _end in scene_list:
        cuts.append(start.get_seconds())
    log.info("scene_cuts_detected", count=len(cuts), video=str(video_path))
    return cuts
