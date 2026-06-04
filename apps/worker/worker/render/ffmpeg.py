"""FFmpeg helpers for clip rendering.

This is where the "industry-standard portrait look" lives:

For a 9:16 vertical clip from a 16:9 source we don't just letterbox with
black bars — we render a scaled, heavily-blurred copy of the same frame
behind the centered original. This is the same effect that TikTok,
Instagram Reels, and YouTube Shorts use for landscape uploads.

For audio we apply ``loudnorm`` so consecutive clips don't jump in volume.
"""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class RenderSpec:
    source_path: Path
    output_path: Path
    start_seconds: float
    end_seconds: float
    aspect: str  # "9:16" | "16:9" | "1:1"
    width: int  # final width in pixels
    height: int
    audio_loudnorm: bool = True
    crf: int = 20  # 18-23 reasonable; 20 is good quality/size trade
    preset: str = "medium"

    @property
    def duration(self) -> float:
        return max(0.1, self.end_seconds - self.start_seconds)


def target_resolution(aspect: str) -> tuple[int, int]:
    """Default output size for each aspect (modern TikTok/Reels/Shorts spec)."""
    if aspect == "9:16":
        return (1080, 1920)
    if aspect == "1:1":
        return (1080, 1080)
    return (1920, 1080)  # 16:9


def _build_video_filter(spec: RenderSpec) -> str:
    """Two-layer filter graph: blurred background fill + centered original.

    Works whether the source is landscape, portrait, or square because
    ``force_original_aspect_ratio`` does the right thing in both branches.
    Output frame is always exactly width x height.
    """
    W, H = spec.width, spec.height
    # Split incoming stream: one path becomes the centered foreground, the
    # other becomes the blurred background. We use yuv420p so the result
    # plays everywhere.
    return (
        f"[0:v]split=2[bg][fg];"
        # Background: cover the full canvas, heavily blurred and slightly
        # darkened so it never competes with the foreground subject.
        f"[bg]scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},boxblur=20:1,eq=brightness=-0.10:saturation=1.1[bgblur];"
        # Foreground: fit inside the canvas preserving aspect ratio.
        f"[fg]scale={W}:{H}:force_original_aspect_ratio=decrease[fgfit];"
        # Stack: paste foreground centered on the blurred background.
        f"[bgblur][fgfit]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p"
    )


def _build_cmd(spec: RenderSpec) -> list[str]:
    vf = _build_video_filter(spec)
    af = (
        "loudnorm=I=-16:TP=-1.5:LRA=11"
        if spec.audio_loudnorm
        else "anull"
    )
    return [
        "ffmpeg",
        "-y",
        # Seeking BEFORE -i is fast (input seek); placing it after -i is
        # frame-accurate but slow. For clip cuts we use both: input seek to
        # the nearest keyframe, then -ss again with -accurate_seek on output
        # for frame precision. ffmpeg's modern `-ss <input> -t <dur>` is fine
        # when paired with re-encoding (which we do for the blur filter).
        "-ss",
        f"{max(0.0, spec.start_seconds):.3f}",
        "-i",
        str(spec.source_path),
        "-t",
        f"{spec.duration:.3f}",
        "-filter_complex",
        vf,
        "-af",
        af,
        "-c:v",
        "libx264",
        "-preset",
        spec.preset,
        "-crf",
        str(spec.crf),
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "high",
        "-movflags",
        "+faststart",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        str(spec.output_path),
    ]


def _render_sync(spec: RenderSpec) -> None:
    spec.output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = _build_cmd(spec)
    log.info("render_ffmpeg_start", duration=spec.duration, aspect=spec.aspect)
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        msg = result.stderr.decode(errors="replace")
        # Keep the last ~30 lines so error messages stay readable in the UI.
        tail = "\n".join(msg.strip().splitlines()[-30:])
        raise RuntimeError(f"ffmpeg failed (exit {result.returncode}):\n{tail}")
    if not spec.output_path.exists() or spec.output_path.stat().st_size == 0:
        raise RuntimeError(f"ffmpeg produced no output at {spec.output_path}")


async def render_clip(spec: RenderSpec) -> None:
    await asyncio.to_thread(_render_sync, spec)


def _burn_subtitles_sync(
    video_path: Path,
    ass_path: Path,
    output_path: Path,
    *,
    fonts_dir: Path | None,
) -> None:
    """Burn an ASS subtitle file into a video. Used by the caption pipeline.

    Note: on Windows ffmpeg's subtitles filter needs the path escaped with
    forward slashes and the drive colon escaped, e.g. ``D\\:/foo/bar.ass``.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    posix = ass_path.as_posix().replace(":", r"\:")
    sub_filter = f"subtitles='{posix}'"
    if fonts_dir is not None:
        sub_filter += f":fontsdir='{fonts_dir.as_posix()}'"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        sub_filter,
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-c:a",
        "copy",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        msg = result.stderr.decode(errors="replace")
        tail = "\n".join(msg.strip().splitlines()[-30:])
        raise RuntimeError(f"ffmpeg subtitle burn failed:\n{tail}")


async def burn_subtitles(
    video_path: Path,
    ass_path: Path,
    output_path: Path,
    *,
    fonts_dir: Path | None = None,
) -> None:
    await asyncio.to_thread(
        _burn_subtitles_sync, video_path, ass_path, output_path, fonts_dir=fonts_dir
    )
