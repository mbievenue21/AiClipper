"""Optional OCR for game HUD / killfeed terms (PaddleOCR)."""

from __future__ import annotations

import re
from typing import Any
import subprocess
import tempfile
from pathlib import Path

import structlog

from ..config import get_settings

log = structlog.get_logger(__name__)

_OCR = None
_OCR_FAILED = False

_VALORANT_TERMS = re.compile(
    r"\b(ACE|CLUTCH|QUAD|TRIPLE|DOUBLE|FLAWLESS|THRIFTY|"
    r"SPIKE|PLANTED|DEFUSE|WON|LOST|ELIMINATED)\b",
    re.IGNORECASE,
)


def ocr_enabled() -> bool:
    return get_settings().profile_ocr_enabled


def _get_ocr():
    global _OCR, _OCR_FAILED
    if _OCR_FAILED:
        return None
    if _OCR is not None:
        return _OCR
    try:
        from paddleocr import PaddleOCR

        _OCR = PaddleOCR(
            use_angle_cls=False,
            lang="en",
            show_log=False,
        )
        return _OCR
    except Exception as exc:
        _OCR_FAILED = True
        log.warning("paddleocr_unavailable", error=str(exc))
        return None


def _extract_frame(video_path: Path, timestamp: float, dest: Path) -> bool:
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{timestamp:.3f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode == 0 and dest.exists() and dest.stat().st_size > 0


def ocr_window(
    video_path: Path | None,
    start_seconds: float,
    end_seconds: float,
    *,
    sample_count: int = 3,
) -> dict[str, Any]:
    """Sample frames in window and extract OCR game terms."""
    if not ocr_enabled() or video_path is None or not video_path.exists():
        return {"ocr_score": 0.0, "ocr_terms": [], "frames_sampled": 0}

    ocr = _get_ocr()
    if ocr is None:
        return {"ocr_score": 0.0, "ocr_terms": [], "frames_sampled": 0}

    duration = max(0.1, end_seconds - start_seconds)
    offsets = [
        start_seconds + duration * (i + 1) / (sample_count + 1)
        for i in range(sample_count)
    ]

    terms: list[str] = []
    sampled = 0
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for i, t in enumerate(offsets):
            frame = tmp_path / f"frame_{i}.jpg"
            if not _extract_frame(video_path, t, frame):
                continue
            sampled += 1
            try:
                result = ocr.ocr(str(frame), cls=False)
            except Exception:
                continue
            if not result:
                continue
            for block in result:
                if not block:
                    continue
                for line in block:
                    if len(line) < 2:
                        continue
                    text = str(line[1][0] if isinstance(line[1], (list, tuple)) else "")
                    for hit in _VALORANT_TERMS.findall(text):
                        terms.append(hit.upper())

    unique = sorted(set(terms))
    score = min(1.0, len(unique) / 3.0) if unique else 0.0
    return {
        "ocr_score": score,
        "ocr_terms": unique,
        "frames_sampled": sampled,
    }
