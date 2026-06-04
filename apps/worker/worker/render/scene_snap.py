"""Snap clip boundaries to nearby scene cuts.

PySceneDetect is heavy (loads the full video). We only run it on a small
window around the proposed clip start and end so we stay fast even for
long source videos.

Behaviour:
- For each boundary (start, end) we look at the +/- ``window_seconds`` range.
- If a scene cut exists in that window, snap to it (preferring the closest).
- Otherwise keep the original boundary.

A scene cut at second X means frame X starts a *new* scene. So for the
*start* of a clip we snap to the cut itself (start of a clean shot) and
for the *end* we snap one frame before the cut (last frame of the shot).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)


def _detect_scene_cuts_sync(
    video_path: Path,
    window_start: float,
    window_end: float,
    threshold: float,
) -> list[float]:
    """Run content-aware scene detection on a sub-range of the video.

    Returns absolute seconds where new scenes begin within [window_start, window_end].
    """
    try:
        from scenedetect import ContentDetector, SceneManager, open_video
    except ImportError:
        log.warning("scenedetect_not_installed")
        return []

    video = open_video(str(video_path))
    fps = video.frame_rate or 30.0
    start_frame = max(0, int(window_start * fps))
    end_frame = int(window_end * fps)
    video.seek(start_frame)

    manager = SceneManager()
    manager.add_detector(ContentDetector(threshold=threshold))
    manager.detect_scenes(video=video, end_time=end_frame)
    scene_list = manager.get_scene_list()

    cuts: list[float] = []
    for start, _end in scene_list:
        cuts.append(start.get_seconds())
    return cuts


async def detect_scene_cuts(
    video_path: Path,
    window_start: float,
    window_end: float,
    *,
    threshold: float = 27.0,
) -> list[float]:
    return await asyncio.to_thread(
        _detect_scene_cuts_sync,
        video_path,
        max(0.0, window_start),
        max(window_start + 0.1, window_end),
        threshold,
    )


def _pick_closest(cuts: list[float], target: float, max_distance: float) -> float | None:
    if not cuts:
        return None
    best = min(cuts, key=lambda c: abs(c - target))
    return best if abs(best - target) <= max_distance else None


async def snap_to_scenes(
    video_path: Path,
    start_seconds: float,
    end_seconds: float,
    *,
    window_seconds: float = 1.5,
    fps_hint: float | None = None,
) -> tuple[float, float, bool]:
    """Return possibly-snapped (start, end) plus a flag whether we moved them.

    We deliberately keep the snap window small (1.5s by default) so we don't
    massively change the clip the AI already chose — we only clean up the
    in/out so the cuts feel intentional.
    """
    fps = fps_hint or 30.0
    frame_dur = 1.0 / fps

    # We need both windows of cuts; do one detection over [start-W, end+W]
    # so we cover both boundaries in a single pass.
    cuts = await detect_scene_cuts(
        video_path,
        start_seconds - window_seconds,
        end_seconds + window_seconds,
    )

    moved = False

    snapped_start = _pick_closest(
        [c for c in cuts if abs(c - start_seconds) <= window_seconds],
        start_seconds,
        window_seconds,
    )
    if snapped_start is not None and abs(snapped_start - start_seconds) > frame_dur:
        start_seconds = max(0.0, snapped_start)
        moved = True

    # For end: snap to a cut just AFTER end, then back off one frame so
    # the cut starts the next scene (not ours).
    cuts_after_end = [c for c in cuts if c >= end_seconds - window_seconds]
    snapped_end = _pick_closest(cuts_after_end, end_seconds, window_seconds)
    if snapped_end is not None and abs(snapped_end - end_seconds) > frame_dur:
        end_seconds = max(start_seconds + 1.0, snapped_end - frame_dur)
        moved = True

    return start_seconds, end_seconds, moved
