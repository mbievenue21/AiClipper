"""Annotated multi-signal timeline for Gemini context.

Merges per-second audio excitement, chat density, and scene cuts into
compact buckets for each candidate window.
"""

from __future__ import annotations

from .audio_features import AudioFeatureSeries
from .chat_features import ChatDensitySeries


def format_candidate_timeline(
    start: float,
    end: float,
    *,
    audio: AudioFeatureSeries | None,
    chat: ChatDensitySeries | None,
    scene_cuts: list[float] | None,
    bucket_seconds: float = 2.0,
    max_lines: int = 8,
) -> str:
    """Build annotated lines like [t=20:14 | audio=0.92 | chat=0.88 | scene_cut]."""
    if end <= start:
        return ""

    cuts = set(int(round(c)) for c in (scene_cuts or []) if start <= c <= end)
    lines: list[str] = []
    t = start
    while t < end and len(lines) < max_lines:
        bucket_end = min(end, t + bucket_seconds)
        audio_val = audio.excitement_window(t, bucket_end) if audio else 0.0
        chat_val = chat.density_window(t, bucket_end) if chat else 0.0
        tags: list[str] = [
            f"audio={audio_val:.2f}",
            f"chat={chat_val:.2f}",
        ]
        sec = int(round(t))
        if sec in cuts:
            tags.append("scene_cut")
        lines.append(f"[t={_fmt_time(t)} | {' | '.join(tags)}]")
        t = bucket_end

    return "\n".join(lines)


def _fmt_time(seconds: float) -> str:
    m = int(seconds // 60)
    s = seconds % 60
    return f"{m}:{s:04.1f}" if m else f"{s:.1f}s"
