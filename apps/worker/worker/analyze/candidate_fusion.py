"""Fuse local peak-anchored candidates with TwelveLabs visual evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .audio_features import AudioFeatureSeries
from .candidates import Candidate
from .chat_features import ChatDensitySeries
from .twelvelabs_convert import overlap_ratio
from ..providers.twelvelabs_types import VisualSegmentResult
from .ranking_weights import RankingWeights

PENALTY_TYPES = {"commentary_only", "dead_air_or_menu", "menu_or_dead_air"}


@dataclass
class FusedCandidate:
    start_seconds: float
    end_seconds: float
    text: str
    audio_score: float
    chat_score: float
    keyword_score: float
    composite_score: float
    segment_indices: list[int] = field(default_factory=list)
    seed_source: str = "transcript"
    audio_peak_at: float | None = None
    chat_peak_at: float | None = None
    visual_peak_at: float | None = None
    visual_score: float = 0.0
    fusion_score: float = 0.0
    moment_type: str = ""
    confidence: float = 0.0
    sources: list[str] = field(default_factory=list)
    visual_evidence: dict[str, Any] = field(default_factory=dict)
    reason_json: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds

    def to_candidate(self) -> Candidate:
        """Convert back to Candidate for Gemini rerank compatibility."""
        c = Candidate(
            start_seconds=self.start_seconds,
            end_seconds=self.end_seconds,
            text=self.text,
            audio_score=self.audio_score,
            chat_score=self.chat_score,
            keyword_score=self.keyword_score,
            composite_score=self.fusion_score or self.composite_score,
            segment_indices=self.segment_indices,
            seed_source=self.seed_source if self.seed_source in ("transcript", "audio_peak", "chat_peak") else "transcript",  # type: ignore[arg-type]
            audio_peak_at=self.audio_peak_at,
            chat_peak_at=self.chat_peak_at,
        )
        c.visual_score = self.visual_score  # type: ignore[attr-defined]
        c.fusion_score = self.fusion_score  # type: ignore[attr-defined]
        c.moment_type = self.moment_type  # type: ignore[attr-defined]
        c.confidence = self.confidence  # type: ignore[attr-defined]
        c.sources = self.sources  # type: ignore[attr-defined]
        c.visual_evidence = self.visual_evidence  # type: ignore[attr-defined]
        c.reason_json = self.reason_json  # type: ignore[attr-defined]
        c.visual_peak_at = self.visual_peak_at  # type: ignore[attr-defined]
        return c


def _visual_candidates_from_segments(
    segments: list[VisualSegmentResult],
    *,
    min_seconds: float,
    max_seconds: float,
) -> list[FusedCandidate]:
    out: list[FusedCandidate] = []
    for seg in segments:
        start = seg.suggested_clip_start_seconds or seg.start_seconds
        end = seg.suggested_clip_end_seconds or seg.end_seconds
        if end - start < min_seconds * 0.5:
            end = start + min_seconds
        if end - start > max_seconds:
            end = start + max_seconds
        source = (
            "twelvelabs_marengo"
            if seg.source_method == "marengo_search"
            else "twelvelabs_pegasus"
        )
        out.append(
            FusedCandidate(
                start_seconds=start,
                end_seconds=end,
                text=seg.description or seg.title or "",
                audio_score=0.0,
                chat_score=0.0,
                keyword_score=0.0,
                composite_score=seg.confidence,
                seed_source=source,
                visual_peak_at=(start + end) / 2.0,
                visual_score=seg.confidence,
                fusion_score=seg.confidence,
                moment_type=seg.segment_type,
                confidence=seg.confidence,
                sources=[source],
                visual_evidence={
                    "segment_type": seg.segment_type,
                    "confidence": seg.confidence,
                    "description": seg.description,
                    "visual_reason": seg.visual_reason,
                    "audio_reason": seg.audio_reason,
                    "speech_reason": seg.speech_reason,
                    "source_method": seg.source_method,
                },
            )
        )
    return out


def _provider_agreement(sources: list[str]) -> float:
    unique = set(sources)
    if len(unique) >= 3:
        return 0.9
    if len(unique) == 2:
        return 0.7
    return 0.35


def _peak_alignment_penalty(
    start: float,
    end: float,
    audio_peak: float | None,
    chat_peak: float | None,
    visual_peak: float | None,
) -> float:
    center = (start + end) / 2.0
    half = max(1.0, (end - start) / 2.0)
    peaks = [p for p in (audio_peak, chat_peak, visual_peak) if p is not None]
    if not peaks:
        return 0.0
    min_dist = min(abs(p - center) for p in peaks)
    return min(0.25, (min_dist / half) * 0.15)


def _commentary_penalty(moment_type: str, visual_score: float, transcript_heavy: bool) -> float:
    if moment_type in PENALTY_TYPES:
        return 0.2
    if transcript_heavy and visual_score < 0.45:
        return 0.12
    return 0.0


def fuse_highlight_candidates(
    local_candidates: list[Candidate],
    visual_segments: list[VisualSegmentResult],
    *,
    audio: AudioFeatureSeries | None = None,
    chat: ChatDensitySeries | None = None,
    scene_cuts: list[float] | None = None,
    min_clip_seconds: float = 20.0,
    max_clip_seconds: float = 60.0,
    weights: RankingWeights | None = None,
) -> list[FusedCandidate]:
    """Merge local + TwelveLabs candidates with weighted fusion scoring."""
    fused: list[FusedCandidate] = [
        FusedCandidate(
            start_seconds=c.start_seconds,
            end_seconds=c.end_seconds,
            text=c.text,
            audio_score=c.audio_score,
            chat_score=c.chat_score,
            keyword_score=c.keyword_score,
            composite_score=c.composite_score,
            segment_indices=c.segment_indices,
            seed_source=c.seed_source,
            audio_peak_at=c.audio_peak_at,
            chat_peak_at=c.chat_peak_at,
            sources=[c.seed_source],
            moment_type="",
            confidence=c.composite_score,
        )
        for c in local_candidates
    ]
    fused.extend(
        _visual_candidates_from_segments(
            visual_segments,
            min_seconds=min_clip_seconds,
            max_seconds=max_clip_seconds,
        )
    )

    merged: list[FusedCandidate] = []
    for cand in sorted(fused, key=lambda x: x.composite_score, reverse=True):
        match_idx: int | None = None
        for i, kept in enumerate(merged):
            overlap = overlap_ratio(
                cand.start_seconds,
                cand.end_seconds,
                kept.start_seconds,
                kept.end_seconds,
            )
            center_dist = abs(
                (cand.start_seconds + cand.end_seconds) / 2
                - (kept.start_seconds + kept.end_seconds) / 2
            )
            same_peak = False
            if cand.visual_peak_at and kept.visual_peak_at:
                same_peak = abs(cand.visual_peak_at - kept.visual_peak_at) < 20.0
            if overlap > 0.45 or center_dist < 20.0 or same_peak:
                match_idx = i
                break

        if match_idx is None:
            merged.append(cand)
            continue

        kept = merged[match_idx]
        kept.sources = list(dict.fromkeys(kept.sources + cand.sources))
        kept.visual_score = max(kept.visual_score, cand.visual_score)
        kept.audio_score = max(kept.audio_score, cand.audio_score)
        kept.chat_score = max(kept.chat_score, cand.chat_score)
        kept.keyword_score = max(kept.keyword_score, cand.keyword_score)
        kept.composite_score = max(kept.composite_score, cand.composite_score)
        if cand.visual_evidence:
            kept.visual_evidence = {**kept.visual_evidence, **cand.visual_evidence}
        if cand.moment_type:
            kept.moment_type = cand.moment_type
        kept.confidence = max(kept.confidence, cand.confidence)

        # Boundary preference: visual when high confidence + commentary drift.
        visual_high = cand.visual_score >= 0.7
        local_commentary = kept.seed_source == "transcript" and kept.audio_peak_at
        if visual_high and (
            cand.moment_type in PENALTY_TYPES
            or (local_commentary and cand.visual_peak_at)
        ):
            kept.start_seconds = min(kept.start_seconds, cand.start_seconds)
            kept.end_seconds = max(kept.end_seconds, cand.end_seconds)
        elif kept.visual_score < 0.5 and cand.visual_score < 0.5:
            kept.start_seconds = min(kept.start_seconds, cand.start_seconds)
            kept.end_seconds = max(kept.end_seconds, cand.end_seconds)

        if cand.audio_peak_at:
            kept.audio_peak_at = cand.audio_peak_at
        if cand.chat_peak_at:
            kept.chat_peak_at = cand.chat_peak_at
        if cand.visual_peak_at:
            kept.visual_peak_at = cand.visual_peak_at
        if cand.text and len(cand.text) > len(kept.text):
            kept.text = cand.text

    scene_list = scene_cuts or []
    for cand in merged:
        transcript_score = cand.keyword_score * 0.6 + (0.4 if cand.text else 0.0)
        scene_score = 0.0
        if scene_list:
            mid = (cand.start_seconds + cand.end_seconds) / 2.0
            if any(abs(mid - sc) < 3.0 for sc in scene_list):
                scene_score = 0.55

        agreement = _provider_agreement(cand.sources)
        commentary_pen = _commentary_penalty(
            cand.moment_type,
            cand.visual_score,
            transcript_heavy=cand.seed_source == "transcript" and bool(cand.text),
        )
        dead_air_pen = 0.15 if cand.moment_type in PENALTY_TYPES else 0.0
        off_center_pen = _peak_alignment_penalty(
            cand.start_seconds,
            cand.end_seconds,
            cand.audio_peak_at,
            cand.chat_peak_at,
            cand.visual_peak_at,
        )

        w = weights or RankingWeights()
        fusion = (
            w.fusion_visual * cand.visual_score
            + w.fusion_chat * cand.chat_score
            + w.fusion_audio * cand.audio_score
            + w.fusion_transcript * transcript_score
            + w.fusion_alignment * (1.0 - off_center_pen * 4)
            + w.fusion_scene * scene_score
            + w.fusion_agreement * agreement
            - commentary_pen
            - dead_air_pen
            - off_center_pen
        )
        cand.fusion_score = float(max(0.0, min(1.0, fusion)))
        cand.reason_json = {
            "sources": cand.sources,
            "seed_source": cand.seed_source,
            "moment_type": cand.moment_type or None,
            "scores": {
                "local": cand.composite_score,
                "transcript": transcript_score,
                "audio": cand.audio_score,
                "chat": cand.chat_score,
                "scene": scene_score,
                "visual": cand.visual_score,
                "provider_agreement": agreement,
                "fusion": cand.fusion_score,
            },
            "peaks": {
                "audio_peak_at": cand.audio_peak_at,
                "chat_peak_at": cand.chat_peak_at,
                "visual_peak_at": cand.visual_peak_at,
            },
            "twelvelabs": {
                "used": bool(cand.visual_evidence),
                **cand.visual_evidence,
            }
            if cand.visual_evidence
            else {"used": False},
            "penalties": {
                "commentary_heavy": commentary_pen,
                "dead_air": dead_air_pen,
                "off_center_peak": off_center_pen,
            },
        }

    merged.sort(key=lambda c: c.fusion_score, reverse=True)
    return merged
