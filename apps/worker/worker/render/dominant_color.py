"""Extract a clip's dominant color for caption gradients.

We use the median frame so we don't get tripped up by a single bright cut.
"""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
from pathlib import Path


def _grab_middle_frame_sync(video_path: Path, at_seconds: float, dest_jpg: Path) -> None:
    dest_jpg.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{max(0.0, at_seconds):.3f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-q:v",
        "3",
        str(dest_jpg),
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors="replace")[:500])


def _dominant_color_sync(video_path: Path, duration_seconds: float) -> str:
    """Return a hex string like "#1a2b3c" for the dominant color of the middle frame."""
    try:
        from PIL import Image  # type: ignore[import-not-found]
    except ImportError:
        # No Pillow → return a neutral fallback.
        return "#1a1a1a"

    middle = max(0.1, duration_seconds / 2.0)
    with tempfile.TemporaryDirectory() as tmpdir:
        jpg = Path(tmpdir) / "frame.jpg"
        _grab_middle_frame_sync(video_path, middle, jpg)
        with Image.open(jpg) as im:
            # Resize first so quantize is fast.
            small = im.convert("RGB").resize((160, 90))
            # Quantize to 5 colors and pick the most common non-very-dark one.
            quant = small.quantize(colors=5, kmeans=1)
            palette = quant.getpalette() or []
            counts = sorted(quant.getcolors() or [], reverse=True)
            for count, idx in counts:
                r, g, b = palette[idx * 3 : idx * 3 + 3]
                # Skip near-black pixels (letterboxing, etc.).
                if r + g + b > 60:
                    return f"#{r:02x}{g:02x}{b:02x}"
            # Everything was nearly black — return what we have.
            if counts:
                _, idx = counts[0]
                r, g, b = palette[idx * 3 : idx * 3 + 3]
                return f"#{r:02x}{g:02x}{b:02x}"
            return "#1a1a1a"


async def extract_dominant_color(
    video_path: Path, duration_seconds: float
) -> str:
    return await asyncio.to_thread(_dominant_color_sync, video_path, duration_seconds)


# ---------------------------------------------------------------------------
# Color math helpers used by both the worker (ASS subtitle generation) and
# anyone who wants to build a caption gradient programmatically.
# ---------------------------------------------------------------------------


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"


def luminance(hex_color: str) -> float:
    r, g, b = hex_to_rgb(hex_color)
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


def contrasting_pair(dominant_hex: str) -> tuple[str, str]:
    """Pick a (primary, accent) caption color pair that pops against the clip.

    Strategy: if the clip is dark, primary = warm yellow, accent = white.
    If the clip is bright, primary = deep blue/purple, accent = pure white.
    Either way we keep the contrast high so captions stay readable on
    motion/varied frames.
    """
    if luminance(dominant_hex) < 0.45:
        return ("#FFD400", "#FFFFFF")  # bright yellow on dark
    return ("#0F2DA8", "#FFFFFF")  # deep indigo on bright


def shift_toward(hex_a: str, hex_b: str, t: float) -> str:
    """Linear interpolate between two hex colors. t in [0,1]."""
    ar, ag, ab = hex_to_rgb(hex_a)
    br, bg, bb = hex_to_rgb(hex_b)
    return rgb_to_hex(
        int(ar + (br - ar) * t),
        int(ag + (bg - ag) * t),
        int(ab + (bb - ab) * t),
    )
