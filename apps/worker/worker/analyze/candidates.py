"""Sliding-window candidate generation from transcript + audio + chat.

We don't ask Gemini to find clips from scratch — that's expensive and the
results are flaky. Instead:

1. Walk the transcript segment-by-segment.
2. Greedily grow a window from each segment until it hits ``min_seconds``.
3. Keep growing until either ``max_seconds`` or a long pause is hit.
4. Score the window by blending audio excitement, chat density, and a
   light keyword-density boost.
5. Apply non-maximum suppression so overlapping windows don't dominate
   the top-K list.

The output is a list of candidates that the Gemini reranker can then
title and pick from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .audio_features import AudioFeatureSeries
from .chat_features import ChatDensitySeries

# Light keyword signal — these phrases tend to mark interesting moments.
# Intentionally short list; Gemini does the heavy semantic lifting later.
_KEYWORD_PATTERN = re.compile(
    r"\b("
    r"oh my god|holy|insane|crazy|wait|watch this|let'?s go|let me show|"
    r"the secret|the trick|the key|the trick is|here'?s why|here is why|"
    r"never|always|biggest|most important|game changer|breakthrough|"
    r"shocking|unbelievable|incredible|amazing|"
    r"actually|surprisingly|to be honest|honestly|"
    r"funny|hilarious|laughing|lmao|"
    r"\bclip(?:ped)? (?:that|this)?"
    r")\b",
    re.IGNORECASE,
)


@dataclass
class Segment:
    start_seconds: float
    end_seconds: float
    text: str


@dataclass
class Candidate:
    start_seconds: float
    end_seconds: float
    text: str  # joined transcript text in the window
    audio_score: float
    chat_score: float
    keyword_score: float
    composite_score: float  # 0..1
    segment_indices: list[int] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_seconds": self.start_seconds,
            "end_seconds": self.end_seconds,
            "duration_seconds": self.duration_seconds,
            "text": self.text,
            "audio_score": self.audio_score,
            "chat_score": self.chat_score,
            "keyword_score": self.keyword_score,
            "composite_score": self.composite_score,
        }


def _keyword_density(text: str) -> float:
    """Return 0..1 based on how many salient phrases appear."""
    if not text:
        return 0.0
    hits = len(_KEYWORD_PATTERN.findall(text))
    # Saturate quickly — 3+ hits is already a strong signal.
    return min(1.0, hits / 3.0)


def generate_candidates(
    segments: list[Segment],
    *,
    audio: AudioFeatureSeries | None,
    chat: ChatDensitySeries | None,
    min_seconds: float,
    max_seconds: float,
    target_count: int,
) -> list[Candidate]:
    """Slide a window over segments and score each."""
    if not segments:
        return []

    # Weights — chat is only meaningful if we actually have a chat track.
    has_chat = bool(chat and chat.total_messages > 0)
    w_audio, w_chat, w_keyword = (
        (0.45, 0.40, 0.15) if has_chat else (0.75, 0.0, 0.25)
    )

    candidates: list[Candidate] = []
    n = len(segments)
    for i in range(n):
        start_t = segments[i].start_seconds
        end_t = segments[i].end_seconds
        text_parts: list[str] = [segments[i].text]
        idxs = [i]

        # Greedily extend until we hit min_seconds (then keep extending until
        # we approach max_seconds or hit a long silence between segments).
        for j in range(i + 1, n):
            gap = segments[j].start_seconds - end_t
            new_duration = segments[j].end_seconds - start_t
            if new_duration > max_seconds:
                break
            # Allow extending past min_seconds only if there's no big pause.
            if new_duration >= min_seconds and gap > 2.5:
                break
            end_t = segments[j].end_seconds
            text_parts.append(segments[j].text)
            idxs.append(j)

        duration = end_t - start_t
        # Skip candidates that are too short to be useful even after growing.
        if duration < min_seconds * 0.75:
            continue

        text = " ".join(t.strip() for t in text_parts if t and t.strip())
        audio_score = audio.excitement_window(start_t, end_t) if audio else 0.0
        chat_score = chat.density_window(start_t, end_t) if chat else 0.0
        kw_score = _keyword_density(text)

        composite = (
            w_audio * audio_score + w_chat * chat_score + w_keyword * kw_score
        )

        candidates.append(
            Candidate(
                start_seconds=float(start_t),
                end_seconds=float(end_t),
                text=text,
                audio_score=float(audio_score),
                chat_score=float(chat_score),
                keyword_score=float(kw_score),
                composite_score=float(min(1.0, composite)),
                segment_indices=idxs,
            )
        )

    if not candidates:
        return []

    candidates.sort(key=lambda c: c.composite_score, reverse=True)
    suppressed = _non_max_suppression(candidates, iou_threshold=0.5)
    # Cap at max(15, 3*target_count) so Gemini has enough variety but the
    # prompt doesn't get absurd.
    cap = max(15, 3 * target_count)
    return suppressed[:cap]


def _non_max_suppression(
    candidates: list[Candidate], *, iou_threshold: float
) -> list[Candidate]:
    """Greedy NMS over 1D intervals."""
    kept: list[Candidate] = []
    for c in candidates:
        overlaps = False
        for k in kept:
            iou = _interval_iou(
                (c.start_seconds, c.end_seconds), (k.start_seconds, k.end_seconds)
            )
            if iou >= iou_threshold:
                overlaps = True
                break
        if not overlaps:
            kept.append(c)
    return kept


def _interval_iou(a: tuple[float, float], b: tuple[float, float]) -> float:
    lo = max(a[0], b[0])
    hi = min(a[1], b[1])
    inter = max(0.0, hi - lo)
    union = (a[1] - a[0]) + (b[1] - b[0]) - inter
    return 0.0 if union <= 0 else inter / union
