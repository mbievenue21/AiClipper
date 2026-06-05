"""POC: multimodal Gemini boundary correction on suspect candidates.

Only runs when GEMINI_MULTIMODAL_ENABLED=true and a source video path is
available. Targets candidates where audio peak precedes speech (commentary drift).
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from ..config import get_settings
from .candidates import Candidate
from .gemini import LlmPick

log = structlog.get_logger(__name__)


@dataclass
class MultimodalAdjustment:
    candidate_index: int
    adjusted_start_seconds: float | None
    adjusted_end_seconds: float | None
    boundary_reason: str


def is_multimodal_enabled() -> bool:
    s = get_settings()
    return bool(s.gemini_api_key and s.gemini_multimodal_enabled)


def _multimodal_config(model: str, types: Any, *, max_tokens: int):
    """Per-family config: Gemini 3.x uses thinking_level + media_resolution,
    no temperature; older models keep a low temperature."""
    kwargs: dict[str, Any] = {
        "response_mime_type": "application/json",
        "max_output_tokens": max_tokens,
    }
    if model.lower().startswith("gemini-3"):
        level = (get_settings().gemini_thinking_level or "low").strip().lower()
        if level not in ("minimal", "low", "medium", "high"):
            level = "low"
        if "pro" in model.lower() and level == "minimal":
            level = "low"
        kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=level)
    else:
        kwargs["temperature"] = 0.2
    return types.GenerateContentConfig(**kwargs)


def _extract_clip_sync(
    source_path: Path,
    start: float,
    end: float,
    out_path: Path,
) -> None:
    duration = max(1.0, end - start)
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(max(0.0, start)),
        "-i",
        str(source_path),
        "-t",
        str(duration),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[:500] or "ffmpeg clip extract failed")


def _suspect_candidates(candidates: list[Candidate], limit: int = 10) -> list[int]:
    """Candidates needing multimodal boundary refinement (complements TwelveLabs)."""
    suspects: list[tuple[float, int]] = []
    for i, c in enumerate(candidates):
        score = 0.0
        offset = c.peak_offset_from_start
        if offset is not None and offset < -2.0:
            score += abs(offset)
        if c.seed_source in ("audio_peak", "chat_peak"):
            score += 2.0

        visual_ev = getattr(c, "visual_evidence", {}) or {}
        visual_score = getattr(c, "visual_score", 0.0)
        if visual_ev:
            sug_start = visual_ev.get("suggested_clip_start_seconds")
            if sug_start is not None and abs(float(sug_start) - c.start_seconds) > 5.0:
                score += 3.0
        if c.keyword_score >= 0.34 and visual_score < 0.45:
            score += 2.5
        if getattr(c, "fusion_score", 0.0) > 0.6 and c.audio_score < 0.4:
            score += 1.5

        penalties = (getattr(c, "reason_json", {}) or {}).get("penalties") or {}
        if float(penalties.get("commentary_heavy") or 0) > 0.08:
            score += 2.0

        if score > 0:
            suspects.append((score, i))
    suspects.sort(reverse=True)
    return [idx for _, idx in suspects[:limit]]


def refine_boundaries_multimodal(
    candidates: list[Candidate],
    picks: list[LlmPick],
    *,
    source_video_path: Path,
    max_pre_roll: float,
    max_clip_seconds: float,
) -> list[LlmPick]:
    """Apply multimodal boundary hints to existing picks. Best-effort."""
    if not is_multimodal_enabled() or not source_video_path.exists():
        return picks
    if not picks:
        return picks

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return picks

    settings = get_settings()
    client = genai.Client(api_key=settings.gemini_api_key)
    suspect_idxs = set(_suspect_candidates(candidates))
    updated = list(picks)

    for pick in updated:
        if pick.candidate_index not in suspect_idxs:
            continue
        c = candidates[pick.candidate_index]
        pad = min(max_pre_roll, 10.0)
        clip_start = max(0.0, c.start_seconds - pad)
        clip_end = min(c.end_seconds + pad, c.end_seconds + max_clip_seconds)

        with tempfile.TemporaryDirectory() as tmp:
            clip_path = Path(tmp) / "window.mp4"
            try:
                _extract_clip_sync(source_video_path, clip_start, clip_end, clip_path)
            except Exception as exc:
                log.warning("multimodal_extract_failed", error=str(exc))
                continue

            if not clip_path.exists() or clip_path.stat().st_size < 1000:
                continue

            uploaded = client.files.upload(file=str(clip_path))
            prompt = (
                "This is a short gaming/stream clip window. Identify where the "
                "visual/audio CLIMAX occurs vs post-play COMMENTARY. Return JSON: "
                '{"climax_offset_seconds": <float offset from clip start>, '
                '"is_commentary_only": <bool>, "suggested_trim_start_offset": <float>, '
                '"suggested_trim_end_offset": <float>, "reason": "<string>"}. '
                f"Clip window starts at {clip_start:.1f}s in the full VOD. "
                f"Transcript excerpt: {c.text[:300]}"
            )
            mm_model = settings.gemini_multimodal_model
            try:
                response = client.models.generate_content(
                    model=mm_model,
                    contents=[uploaded, prompt],
                    config=_multimodal_config(mm_model, types, max_tokens=512),
                )
            except Exception as exc:
                log.warning("multimodal_gemini_failed", model=mm_model, error=str(exc))
                continue

            import json

            raw = (response.text or "").strip()
            if not raw:
                continue
            try:
                parsed: dict[str, Any] = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if parsed.get("is_commentary_only"):
                pick.llm_score *= 0.5
                pick.reason_tags = list(pick.reason_tags) + ["commentary_only"]
                continue

            trim_start = parsed.get("suggested_trim_start_offset")
            trim_end = parsed.get("suggested_trim_end_offset")
            if trim_start is not None:
                new_start = clip_start + float(trim_start)
                lower = max(0.0, c.start_seconds - max_pre_roll)
                pick.adjusted_start_seconds = max(lower, min(new_start, c.start_seconds))
            if trim_end is not None:
                new_end = clip_start + float(trim_end)
                upper = c.end_seconds + max_pre_roll
                pick.adjusted_end_seconds = max(c.end_seconds, min(new_end, upper))
            reason = str(parsed.get("reason") or "").strip()
            if reason:
                pick.boundary_reason = (pick.boundary_reason + " " + reason).strip()[:200]

            log.info(
                "multimodal_adjusted",
                candidate_index=pick.candidate_index,
                start=pick.adjusted_start_seconds,
                end=pick.adjusted_end_seconds,
            )

    return updated
