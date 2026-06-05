"""Multi-source candidate generation from transcript + audio peaks + chat peaks.

We don't ask Gemini to find clips from scratch — that's expensive and the
results are flaky. Instead:

1. Seed windows from transcript segments (speech-anchored).
2. Seed windows from audio excitement peaks (action-anchored).
3. Seed windows from chat density peaks (reaction-anchored).
4. Score each window by blending audio, chat, keywords, and peak alignment.
5. Apply non-maximum suppression so overlapping windows don't dominate.

The output is a list of candidates that the Gemini reranker can then
title and pick from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from .audio_features import AudioFeatureSeries
from .chat_features import ChatDensitySeries

# Light keyword signal — these phrases tend to mark interesting moments.
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

SeedSource = Literal["transcript", "audio_peak", "chat_peak"]


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
    seed_source: SeedSource = "transcript"
    audio_peak_at: float | None = None
    chat_peak_at: float | None = None
    # TwelveLabs fusion extensions (optional)
    visual_score: float = 0.0
    fusion_score: float = 0.0
    visual_peak_at: float | None = None
    moment_type: str = ""
    confidence: float = 0.0
    sources: list[str] = field(default_factory=list)
    visual_evidence: dict[str, Any] = field(default_factory=dict)
    reason_json: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds

    @property
    def peak_offset_from_start(self) -> float | None:
        """Seconds from window start to the dominant signal peak (negative = peak before speech)."""
        peaks = [p for p in (self.audio_peak_at, self.chat_peak_at) if p is not None]
        if not peaks:
            return None
        dominant = max(peaks, key=lambda t: abs(t - (self.start_seconds + self.duration_seconds / 2)))
        return dominant - self.start_seconds

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
            "seed_source": self.seed_source,
            "audio_peak_at": self.audio_peak_at,
            "chat_peak_at": self.chat_peak_at,
            "peak_offset_from_start": self.peak_offset_from_start,
        }


def _keyword_density(text: str) -> float:
    if not text:
        return 0.0
    hits = len(_KEYWORD_PATTERN.findall(text))
    return min(1.0, hits / 3.0)


def _find_peaks(values: list[float], *, min_height: float, min_distance: int) -> list[int]:
    """Simple peak finder without scipy."""
    if not values:
        return []
    peaks: list[int] = []
    n = len(values)
    for i in range(1, n - 1):
        if values[i] < min_height:
            continue
        if values[i] <= values[i - 1] or values[i] < values[i + 1]:
            continue
        if peaks and (i - peaks[-1]) < min_distance:
            if values[i] > values[peaks[-1]]:
                peaks[-1] = i
            continue
        peaks.append(i)
    return peaks


def _transcript_for_window(
    segments: list[Segment],
    start_t: float,
    end_t: float,
) -> tuple[str, list[int]]:
    parts: list[str] = []
    idxs: list[int] = []
    for i, seg in enumerate(segments):
        if seg.end_seconds < start_t or seg.start_seconds > end_t:
            continue
        if seg.text and seg.text.strip():
            parts.append(seg.text.strip())
        idxs.append(i)
    return " ".join(parts), idxs


def _peak_in_window(
    series: list[float] | None,
    start_t: float,
    end_t: float,
) -> float | None:
    if not series or end_t <= start_t:
        return None
    i = max(0, int(round(start_t)))
    j = min(len(series), int(round(end_t)) + 1)
    if j <= i:
        return None
    slice_ = series[i:j]
    if not slice_:
        return None
    peak_idx = max(range(len(slice_)), key=lambda k: slice_[k])
    return float(i + peak_idx)


def _score_candidate(
    *,
    start_t: float,
    end_t: float,
    text: str,
    audio: AudioFeatureSeries | None,
    chat: ChatDensitySeries | None,
    w_audio: float,
    w_chat: float,
    w_keyword: float,
    seed_source: SeedSource,
    anchor_peak: float | None = None,
) -> tuple[float, float, float, float, float | None, float | None]:
    audio_score = audio.excitement_window(start_t, end_t) if audio else 0.0
    chat_score = chat.density_window(start_t, end_t) if chat else 0.0
    kw_score = _keyword_density(text)

    audio_peak = _peak_in_window(
        [s["excitement"] for s in audio.samples] if audio else None,
        start_t,
        end_t,
    )
    chat_peak = _peak_in_window(chat.normalised if chat else None, start_t, end_t)

    composite = w_audio * audio_score + w_chat * chat_score + w_keyword * kw_score

    # Bonus when the seed peak sits near the window center (action-aligned).
    if anchor_peak is not None:
        center = (start_t + end_t) / 2.0
        dist = abs(anchor_peak - center)
        half = max(1.0, (end_t - start_t) / 2.0)
        alignment = max(0.0, 1.0 - dist / half)
        composite += 0.12 * alignment
    elif seed_source in ("audio_peak", "chat_peak"):
        composite += 0.08

    # Penalty when speech-heavy but audio/chat peaks are far from center (commentary drift).
    if seed_source == "transcript" and text:
        peaks = [p for p in (audio_peak, chat_peak) if p is not None]
        if peaks:
            center = (start_t + end_t) / 2.0
            min_dist = min(abs(p - center) for p in peaks)
            if min_dist > (end_t - start_t) * 0.35:
                composite *= 0.85

    return (
        float(audio_score),
        float(chat_score),
        float(kw_score),
        float(min(1.0, composite)),
        audio_peak,
        chat_peak,
    )


def _transcript_candidates(
    segments: list[Segment],
    *,
    audio: AudioFeatureSeries | None,
    chat: ChatDensitySeries | None,
    min_seconds: float,
    max_seconds: float,
    w_audio: float,
    w_chat: float,
    w_keyword: float,
) -> list[Candidate]:
    candidates: list[Candidate] = []
    n = len(segments)
    for i in range(n):
        start_t = segments[i].start_seconds
        end_t = segments[i].end_seconds
        text_parts: list[str] = [segments[i].text]
        idxs = [i]

        for j in range(i + 1, n):
            gap = segments[j].start_seconds - end_t
            new_duration = segments[j].end_seconds - start_t
            if new_duration > max_seconds:
                break
            if new_duration >= min_seconds and gap > 2.5:
                break
            end_t = segments[j].end_seconds
            text_parts.append(segments[j].text)
            idxs.append(j)

        duration = end_t - start_t
        if duration < min_seconds * 0.75:
            continue

        text = " ".join(t.strip() for t in text_parts if t and t.strip())
        a, c, kw, composite, audio_peak, chat_peak = _score_candidate(
            start_t=start_t,
            end_t=end_t,
            text=text,
            audio=audio,
            chat=chat,
            w_audio=w_audio,
            w_chat=w_chat,
            w_keyword=w_keyword,
            seed_source="transcript",
        )
        candidates.append(
            Candidate(
                start_seconds=float(start_t),
                end_seconds=float(end_t),
                text=text,
                audio_score=a,
                chat_score=c,
                keyword_score=kw,
                composite_score=composite,
                segment_indices=idxs,
                seed_source="transcript",
                audio_peak_at=audio_peak,
                chat_peak_at=chat_peak,
            )
        )
    return candidates


def _peak_anchored_candidates(
    peaks: list[int],
    *,
    series_len: int,
    min_seconds: float,
    max_seconds: float,
    segments: list[Segment],
    audio: AudioFeatureSeries | None,
    chat: ChatDensitySeries | None,
    w_audio: float,
    w_chat: float,
    w_keyword: float,
    seed_source: SeedSource,
) -> list[Candidate]:
    """Build windows centered on signal peaks."""
    candidates: list[Candidate] = []
    target = (min_seconds + max_seconds) / 2.0
    half = min(target / 2.0, max_seconds / 2.0)

    for peak_idx in peaks:
        peak_t = float(peak_idx)
        start_t = max(0.0, peak_t - half)
        end_t = min(float(series_len - 1), peak_t + half)
        if end_t - start_t < min_seconds * 0.75:
            end_t = min(float(series_len - 1), start_t + min_seconds)
        if end_t - start_t < min_seconds * 0.5:
            continue

        text, idxs = _transcript_for_window(segments, start_t, end_t)
        a, c, kw, composite, audio_peak, chat_peak = _score_candidate(
            start_t=start_t,
            end_t=end_t,
            text=text,
            audio=audio,
            chat=chat,
            w_audio=w_audio,
            w_chat=w_chat,
            w_keyword=w_keyword,
            seed_source=seed_source,
            anchor_peak=peak_t,
        )
        candidates.append(
            Candidate(
                start_seconds=float(start_t),
                end_seconds=float(end_t),
                text=text,
                audio_score=a,
                chat_score=c,
                keyword_score=kw,
                composite_score=composite,
                segment_indices=idxs,
                seed_source=seed_source,
                audio_peak_at=audio_peak if seed_source == "audio_peak" else audio_peak,
                chat_peak_at=chat_peak if seed_source == "chat_peak" else chat_peak,
            )
        )
    return candidates


def generate_candidates(
    segments: list[Segment],
    *,
    audio: AudioFeatureSeries | None,
    chat: ChatDensitySeries | None,
    min_seconds: float,
    max_seconds: float,
    target_count: int,
) -> list[Candidate]:
    """Generate candidates from transcript, audio peaks, and chat peaks."""
    has_chat = bool(chat and chat.total_messages > 0)
    w_audio, w_chat, w_keyword = (
        (0.45, 0.40, 0.15) if has_chat else (0.75, 0.0, 0.25)
    )

    candidates: list[Candidate] = []

    if segments:
        candidates.extend(
            _transcript_candidates(
                segments,
                audio=audio,
                chat=chat,
                min_seconds=min_seconds,
                max_seconds=max_seconds,
                w_audio=w_audio,
                w_chat=w_chat,
                w_keyword=w_keyword,
            )
        )

    if audio and audio.samples:
        excitement = [s["excitement"] for s in audio.samples]
        audio_peaks = _find_peaks(excitement, min_height=0.55, min_distance=int(min_seconds))
        candidates.extend(
            _peak_anchored_candidates(
                audio_peaks,
                series_len=len(excitement),
                min_seconds=min_seconds,
                max_seconds=max_seconds,
                segments=segments,
                audio=audio,
                chat=chat,
                w_audio=w_audio,
                w_chat=w_chat,
                w_keyword=w_keyword,
                seed_source="audio_peak",
            )
        )

    if chat and chat.normalised and chat.total_messages > 0:
        chat_peaks = _find_peaks(chat.normalised, min_height=0.5, min_distance=int(min_seconds))
        candidates.extend(
            _peak_anchored_candidates(
                chat_peaks,
                series_len=len(chat.normalised),
                min_seconds=min_seconds,
                max_seconds=max_seconds,
                segments=segments,
                audio=audio,
                chat=chat,
                w_audio=w_audio,
                w_chat=w_chat,
                w_keyword=w_keyword,
                seed_source="chat_peak",
            )
        )

    if not candidates:
        return []

    candidates.sort(key=lambda c: c.composite_score, reverse=True)
    suppressed = _non_max_suppression(candidates, iou_threshold=0.5)
    cap = max(15, 3 * target_count)
    return suppressed[:cap]


def _non_max_suppression(
    candidates: list[Candidate], *, iou_threshold: float
) -> list[Candidate]:
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
