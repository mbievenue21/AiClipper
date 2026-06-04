"""ffprobe wrapper — reads container/stream metadata from a video file."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VideoProbe:
    duration_seconds: float | None
    width: int | None
    height: int | None
    fps: float | None
    codec: str | None
    size_bytes: int


def probe_video(path: Path) -> VideoProbe:
    """Run ffprobe and return normalized fields for the `videos` table."""
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(proc.stdout)

    fmt = data.get("format") or {}
    duration = _parse_float(fmt.get("duration"))
    size_bytes = int(fmt.get("size") or path.stat().st_size)

    video_stream = _pick_video_stream(data.get("streams") or [])
    width = height = fps = codec = None
    if video_stream:
        width = video_stream.get("width")
        height = video_stream.get("height")
        codec = video_stream.get("codec_name")
        fps = _parse_fps(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate"))

    return VideoProbe(
        duration_seconds=duration,
        width=int(width) if width is not None else None,
        height=int(height) if height is not None else None,
        fps=fps,
        codec=codec,
        size_bytes=size_bytes,
    )


def _pick_video_stream(streams: list[dict]) -> dict | None:
    for stream in streams:
        if stream.get("codec_type") == "video":
            return stream
    return None


def _parse_float(value: str | float | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_fps(value: str | None) -> float | None:
    if not value or value == "0/0":
        return None
    if "/" in value:
        num, den = value.split("/", 1)
        try:
            n, d = float(num), float(den)
            return None if d == 0 else n / d
        except ValueError:
            return None
    return _parse_float(value)
