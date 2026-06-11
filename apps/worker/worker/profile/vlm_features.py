"""Lightweight visual features + optional Gemini VLM window scoring."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import structlog

from ..config import get_settings

log = structlog.get_logger(__name__)


def vlm_enabled() -> bool:
    return get_settings().profile_vlm_enabled


def motion_brightness_delta(
    video_path: Path | None,
    start_seconds: float,
    end_seconds: float,
) -> dict[str, float]:
    """Cheap frame-diff proxy for visual motion (no ML)."""
    if video_path is None or not video_path.exists():
        return {"motion_delta": 0.0, "brightness_delta": 0.0}

    try:
        from PIL import Image
    except ImportError:
        return {"motion_delta": 0.0, "brightness_delta": 0.0}

    mid = (start_seconds + end_seconds) / 2
    with tempfile.TemporaryDirectory() as tmp:
        a = Path(tmp) / "a.jpg"
        b = Path(tmp) / "b.jpg"
        if not _grab_frame(video_path, start_seconds, a):
            return {"motion_delta": 0.0, "brightness_delta": 0.0}
        if not _grab_frame(video_path, mid, b):
            return {"motion_delta": 0.0, "brightness_delta": 0.0}

        img_a = Image.open(a).convert("L").resize((160, 90))
        img_b = Image.open(b).convert("L").resize((160, 90))
        pa = list(img_a.getdata())
        pb = list(img_b.getdata())
        diff = sum(abs(x - y) for x, y in zip(pa, pb, strict=False)) / (len(pa) * 255)
        bright_a = sum(pa) / len(pa) / 255
        bright_b = sum(pb) / len(pb) / 255
        return {
            "motion_delta": min(1.0, diff * 2.5),
            "brightness_delta": min(1.0, abs(bright_b - bright_a) * 3),
        }


def _grab_frame(video_path: Path, t: float, dest: Path) -> bool:
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{t:.3f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode == 0 and dest.exists()


def vlm_highlight_score(
    video_path: Path | None,
    start_seconds: float,
    end_seconds: float,
    *,
    vibe: str = "",
) -> float:
    """Optional Gemini multimodal score for a short candidate window."""
    if not vlm_enabled() or video_path is None:
        return 0.0
    try:
        from ..analyze.gemini import is_configured
        from google import genai
        from google.genai import types as genai_types
    except ImportError:
        return 0.0

    if not is_configured():
        return 0.0

    settings = get_settings()
    if not settings.gemini_api_key:
        return 0.0

    with tempfile.TemporaryDirectory() as tmp:
        clip_path = Path(tmp) / "window.mp4"
        dur = min(12.0, max(2.0, end_seconds - start_seconds))
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{start_seconds:.3f}",
            "-i",
            str(video_path),
            "-t",
            f"{dur:.3f}",
            "-c",
            "copy",
            str(clip_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0 or not clip_path.exists():
            return 0.0

        client = genai.Client(api_key=settings.gemini_api_key)
        uploaded = client.files.upload(file=str(clip_path))
        prompt = (
            "Rate this gaming/streamer clip window 0.0-1.0 for short-form highlight "
            f"potential. Vibe: {vibe or 'reaction highlights'}. "
            "Reply JSON only: {\"score\": number, \"reason\": string}"
        )
        try:
            resp = client.models.generate_content(
                model=settings.gemini_flash_model,
                contents=[uploaded, prompt],
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                ),
            )
            import json

            data = json.loads(resp.text or "{}")
            return float(min(1.0, max(0.0, data.get("score", 0.0))))
        except Exception as exc:
            log.warning("vlm_score_failed", error=str(exc))
            return 0.0
