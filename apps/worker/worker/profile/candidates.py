"""Multi-source candidate generation driven by profile config."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from ..analyze.audio_features import AudioFeatureSeries
from ..analyze.candidates import Segment, _keyword_density, _transcript_for_window
from ..analyze.chat_features import ChatDensitySeries
from ..analyze.signal_peaks import find_peak_indices
from .config import ProfileConfig
from .embeddings import max_phrase_similarity

log = structlog.get_logger(__name__)


@dataclass
class ProfileCandidate:
    start_seconds: float
    end_seconds: float
    text: str
    candidate_sources: list[str] = field(default_factory=list)
    raw_scores: dict[str, float] = field(default_factory=dict)
    audio_peak_at: float | None = None
    chat_peak_at: float | None = None
    confidence: float = 0.0

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds

    def overlap_iou(self, other: ProfileCandidate) -> float:
        overlap = max(
            0.0,
            min(self.end_seconds, other.end_seconds)
            - max(self.start_seconds, other.start_seconds),
        )
        union = (
            max(self.end_seconds, other.end_seconds)
            - min(self.start_seconds, other.start_seconds)
        )
        return overlap / union if union > 0 else 0.0


def _window_around_peak(
    peak_t: float,
    *,
    min_dur: float,
    max_dur: float,
    target_dur: float,
    duration_limit: float,
) -> tuple[float, float]:
    half = target_dur / 2
    start = max(0.0, peak_t - half * 0.6)
    end = start + target_dur
    if end - start < min_dur:
        end = start + min_dur
    if end - start > max_dur:
        end = start + max_dur
    if duration_limit > 0:
        end = min(end, duration_limit)
        start = max(0.0, min(start, end - min_dur))
    return start, end


def _transcript_seeds(
    segments: list[Segment],
    *,
    min_dur: float,
    max_dur: float,
    target_dur: float,
    duration_limit: float,
) -> list[ProfileCandidate]:
    if not segments:
        return []
    out: list[ProfileCandidate] = []
    i = 0
    while i < len(segments):
        start = segments[i].start_seconds
        j = i
        while j < len(segments):
            end = segments[j].end_seconds
            dur = end - start
            if dur > max_dur:
                break
            if dur >= min_dur:
                text, _ = _transcript_for_window(segments, start, end)
                out.append(
                    ProfileCandidate(
                        start_seconds=start,
                        end_seconds=end,
                        text=text,
                        candidate_sources=["transcript"],
                        raw_scores={"keyword": _keyword_density(text)},
                    )
                )
            j += 1
        i += 1
    return out


def _peak_seeds(
    peaks: list[int],
    series_len: int,
    *,
    source: str,
    min_dur: float,
    max_dur: float,
    target_dur: float,
    duration_limit: float,
    segments: list[Segment],
    min_height: float,
) -> list[ProfileCandidate]:
    out: list[ProfileCandidate] = []
    for idx in peaks:
        peak_t = float(idx)
        start, end = _window_around_peak(
            peak_t,
            min_dur=min_dur,
            max_dur=max_dur,
            target_dur=target_dur,
            duration_limit=duration_limit,
        )
        text, _ = _transcript_for_window(segments, start, end)
        cand = ProfileCandidate(
            start_seconds=start,
            end_seconds=end,
            text=text,
            candidate_sources=[source],
            raw_scores={source: min_height},
        )
        if source == "audio_peak":
            cand.audio_peak_at = peak_t
        elif source == "chat_burst":
            cand.chat_peak_at = peak_t
        out.append(cand)
    return out


def _scene_cut_seeds(
    scene_cuts: list[float],
    audio: AudioFeatureSeries,
    *,
    min_dur: float,
    max_dur: float,
    target_dur: float,
    duration_limit: float,
    segments: list[Segment],
    audio_threshold: float,
) -> list[ProfileCandidate]:
    out: list[ProfileCandidate] = []
    for cut in scene_cuts:
        if audio.excitement_at(cut) < audio_threshold * 0.7:
            continue
        start, end = _window_around_peak(
            cut,
            min_dur=min_dur,
            max_dur=max_dur,
            target_dur=target_dur,
            duration_limit=duration_limit,
        )
        text, _ = _transcript_for_window(segments, start, end)
        out.append(
            ProfileCandidate(
                start_seconds=start,
                end_seconds=end,
                text=text,
                candidate_sources=["scene_cut"],
                raw_scores={"scene": audio.excitement_at(cut)},
            )
        )
    return out


def _merge_candidates(
    candidates: list[ProfileCandidate],
    *,
    merge_window: float,
) -> list[ProfileCandidate]:
    if not candidates:
        return []
    sorted_cands = sorted(candidates, key=lambda c: c.start_seconds)
    merged: list[ProfileCandidate] = [sorted_cands[0]]
    for cand in sorted_cands[1:]:
        prev = merged[-1]
        center_dist = abs(
            (cand.start_seconds + cand.end_seconds) / 2
            - (prev.start_seconds + prev.end_seconds) / 2
        )
        if center_dist <= merge_window or prev.overlap_iou(cand) > 0.45:
            prev.start_seconds = min(prev.start_seconds, cand.start_seconds)
            prev.end_seconds = max(prev.end_seconds, cand.end_seconds)
            prev.text = (prev.text + " " + cand.text).strip()
            for src in cand.candidate_sources:
                if src not in prev.candidate_sources:
                    prev.candidate_sources.append(src)
            prev.raw_scores.update(cand.raw_scores)
            if cand.audio_peak_at is not None:
                prev.audio_peak_at = cand.audio_peak_at
            if cand.chat_peak_at is not None:
                prev.chat_peak_at = cand.chat_peak_at
        else:
            merged.append(cand)
    return merged


def _dedupe_candidates(
    candidates: list[ProfileCandidate],
    *,
    overlap_threshold: float,
) -> list[ProfileCandidate]:
    if not candidates:
        return []
    kept: list[ProfileCandidate] = []
    for cand in sorted(candidates, key=lambda c: -sum(c.raw_scores.values())):
        if any(cand.overlap_iou(k) >= overlap_threshold for k in kept):
            continue
        kept.append(cand)
    return kept


def generate_profile_candidates(
    segments: list[Segment],
    *,
    audio: AudioFeatureSeries | None = None,
    chat: ChatDensitySeries | None = None,
    scene_cuts: list[float] | None = None,
    config: ProfileConfig,
    duration_seconds: float = 0.0,
    target_count: int = 15,
) -> list[ProfileCandidate]:
    """Generate merged, deduplicated candidates from multiple signal sources."""
    sources = config.candidate_sources
    min_dur = config.min_duration()
    max_dur = config.max_duration()
    target_dur = config.target_duration()

    seeds: list[ProfileCandidate] = []

    if sources.get("semanticPhrases", True) and config.phrases:
        for seg in segments:
            score, _ = max_phrase_similarity(
                seg.text,
                config.phrases,
                threshold=float(config.thresholds.get("embeddingSimilarityMin", 0.62)),
            )
            if score < 0.55:
                continue
            start = max(0.0, seg.start_seconds - 4.0)
            end = min(
                duration_seconds or seg.end_seconds + target_dur,
                seg.end_seconds + target_dur,
            )
            if end - start < min_dur:
                end = start + min_dur
            if end - start > max_dur:
                end = start + max_dur
            text, _ = _transcript_for_window(segments, start, end)
            seeds.append(
                ProfileCandidate(
                    start_seconds=start,
                    end_seconds=end,
                    text=text,
                    candidate_sources=["semantic_phrase"],
                    raw_scores={"semantic": score},
                )
            )

    if sources.get("transcriptKeywords", True):
        seeds.extend(
            _transcript_seeds(
                segments,
                min_dur=min_dur,
                max_dur=max_dur,
                target_dur=target_dur,
                duration_limit=duration_seconds,
            )
        )

    audio_thresh = float(config.thresholds.get("audioPeakMin", 0.55))
    if sources.get("audioPeaks", True) and audio is not None:
        excitements = [s["excitement"] for s in audio.samples]
        peaks = find_peak_indices(excitements, min_height=audio_thresh, min_distance=8)
        seeds.extend(
            _peak_seeds(
                peaks,
                len(excitements),
                source="audio_peak",
                min_dur=min_dur,
                max_dur=max_dur,
                target_dur=target_dur,
                duration_limit=duration_seconds,
                segments=segments,
                min_height=audio_thresh,
            )
        )

    chat_thresh = float(config.thresholds.get("chatBurstMin", 0.5))
    if sources.get("chatBursts", True) and chat is not None and chat.normalised:
        peaks = find_peak_indices(
            chat.normalised, min_height=chat_thresh, min_distance=8
        )
        seeds.extend(
            _peak_seeds(
                peaks,
                len(chat.normalised),
                source="chat_burst",
                min_dur=min_dur,
                max_dur=max_dur,
                target_dur=target_dur,
                duration_limit=duration_seconds,
                segments=segments,
                min_height=chat_thresh,
            )
        )

    if sources.get("sceneCuts", True) and scene_cuts and audio is not None:
        seeds.extend(
            _scene_cut_seeds(
                scene_cuts,
                audio,
                min_dur=min_dur,
                max_dur=max_dur,
                target_dur=target_dur,
                duration_limit=duration_seconds,
                segments=segments,
                audio_threshold=audio_thresh,
            )
        )

    merged = _merge_candidates(seeds, merge_window=config.merge_window())
    deduped = _dedupe_candidates(merged, overlap_threshold=config.dedupe_threshold())

    cap = max(15, target_count * 3)
    log.info(
        "profile_candidates_generated",
        seeds=len(seeds),
        merged=len(merged),
        deduped=len(deduped),
    )
    return deduped[:cap]
