"""Explainable profile-based candidate scoring."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from ..analyze.audio_features import AudioFeatureSeries
from ..analyze.candidates import Segment
from ..analyze.chat_features import ChatDensitySeries
from .candidates import ProfileCandidate
from .config import ProfileConfig
from pathlib import Path

from .features import extract_window_features
from .ranker import RankerArtifact, features_to_vector

log = structlog.get_logger(__name__)


@dataclass
class SignalBreakdown:
    audio_peak_score: float = 0.0
    keyword_score: float = 0.0
    semantic_phrase_score: float = 0.0
    chat_burst_score: float = 0.0
    scene_score: float = 0.0
    ocr_score: float = 0.0
    duplicate_penalty: float = 0.0
    duration_penalty: float = 0.0
    final_score: float = 0.0
    explanation: str = ""
    matched_keywords: list[str] = field(default_factory=list)
    matched_phrases: list[str] = field(default_factory=list)
    audio_peak_position: float | None = None
    duplicate_warning: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "audioPeakScore": self.audio_peak_score,
            "keywordScore": self.keyword_score,
            "semanticPhraseScore": self.semantic_phrase_score,
            "chatBurstScore": self.chat_burst_score,
            "sceneScore": self.scene_score,
            "ocrScore": self.ocr_score,
            "duplicatePenalty": self.duplicate_penalty,
            "durationPenalty": self.duration_penalty,
            "finalScore": self.final_score,
            "explanation": self.explanation,
            "matchedKeywords": self.matched_keywords,
            "matchedPhrases": self.matched_phrases,
            "audioPeakPosition": self.audio_peak_position,
            "duplicateWarning": self.duplicate_warning,
        }


@dataclass
class ScoredCandidate:
    candidate: ProfileCandidate
    breakdown: SignalBreakdown
    title_suggestion: str = ""
    features: dict[str, Any] | None = None

    @property
    def score(self) -> float:
        return self.breakdown.final_score


def _duration_penalty(
    duration: float,
    *,
    min_dur: float,
    max_dur: float,
    target_dur: float,
    penalty_scale: float,
) -> float:
    if duration < min_dur:
        return penalty_scale * (1.0 - duration / min_dur)
    if duration > max_dur:
        return penalty_scale * min(1.0, (duration - max_dur) / max_dur)
    dist = abs(duration - target_dur) / target_dur
    return penalty_scale * dist * 0.25


def _build_explanation(bd: SignalBreakdown) -> str:
    parts: list[str] = []
    if bd.audio_peak_score >= 0.5:
        parts.append(f"Strong audio peak (score {bd.audio_peak_score:.2f})")
    if bd.keyword_score >= 0.4 and bd.matched_keywords:
        parts.append(f"Keywords: {', '.join(bd.matched_keywords[:5])}")
    if bd.semantic_phrase_score >= 0.4 and bd.matched_phrases:
        parts.append(f"Phrases: {', '.join(bd.matched_phrases[:3])}")
    if bd.chat_burst_score >= 0.4:
        parts.append(f"Chat burst (score {bd.chat_burst_score:.2f})")
    if bd.scene_score >= 0.1:
        parts.append("Visual motion / scene signal")
    if bd.ocr_score >= 0.2:
        parts.append(f"OCR game terms (score {bd.ocr_score:.2f})")
    if bd.duplicate_penalty > 0.1:
        parts.append("Near-duplicate of another candidate")
    if bd.duration_penalty > 0.1:
        parts.append("Duration outside target range")
    if not parts:
        parts.append("Moderate composite signal")
    return "; ".join(parts)


def _title_from_candidate(cand: ProfileCandidate, bd: SignalBreakdown) -> str:
    if bd.matched_keywords:
        return bd.matched_keywords[0].title() + " moment"
    excerpt = cand.text.strip().split(".")[0].strip()
    if excerpt and len(excerpt) <= 80:
        return excerpt
    if excerpt:
        return excerpt[:77] + "…"
    return f"Highlight @ {int(cand.start_seconds)}s"


def score_candidate(
    cand: ProfileCandidate,
    *,
    segments: list[Segment],
    audio: AudioFeatureSeries | None,
    chat: ChatDensitySeries | None,
    scene_cuts: list[float] | None,
    config: ProfileConfig,
    duration_seconds: float = 0.0,
    duplicate_of: bool = False,
    video_path: Path | None = None,
    vibe: str = "",
    ranker: RankerArtifact | None = None,
    ranker_blend: float = 0.35,
) -> ScoredCandidate:
    """Score a single candidate with explainable signal breakdown."""
    weights = config.score_weights
    penalties = config.penalties

    feats = extract_window_features(
        start_seconds=cand.start_seconds,
        end_seconds=cand.end_seconds,
        segments=segments,
        audio=audio,
        chat=chat,
        scene_cuts=scene_cuts,
        config=config,
        duration_seconds=duration_seconds,
        candidate_sources=cand.candidate_sources,
        video_path=video_path,
        vibe=vibe,
    )

    audio_peak = float(feats.audio.get("peak_z_score", 0.0))
    keyword = float(feats.transcript.get("keyword_score", 0.0))
    anti_kw = float(feats.transcript.get("anti_keyword_score", 0.0))
    phrase = float(feats.transcript.get("phrase_score", 0.0))
    chat_burst = float(feats.chat.get("burst_z_score", 0.0))
    scene = float(feats.visual.get("scene_cut_bonus", 0.0))
    scene += float(feats.visual.get("motion_delta", 0.0)) * 0.25
    scene += float(feats.visual.get("vlm_score", 0.0)) * 0.35
    scene = min(1.0, scene)
    ocr = float(feats.visual.get("ocr_score", 0.0))

    matched_kw = list(feats.transcript.get("matched_keywords") or [])
    matched_phr = list(feats.transcript.get("matched_phrases") or [])

    dur_pen = _duration_penalty(
        cand.duration_seconds,
        min_dur=config.min_duration(),
        max_dur=config.max_duration(),
        target_dur=config.target_duration(),
        penalty_scale=float(penalties.get("tooLong", 0.2)),
    )
    if cand.duration_seconds < config.min_duration():
        dur_pen = max(
            dur_pen,
            float(penalties.get("tooShort", 0.3))
            * (1.0 - cand.duration_seconds / config.min_duration()),
        )

    weak_text_pen = 0.0
    word_count = float(feats.transcript.get("word_count", 0))
    if word_count < 5:
        weak_text_pen = float(penalties.get("weakTranscript", 0.15))

    dup_pen = float(penalties.get("duplicate", 0.25)) if duplicate_of else 0.0

    raw = (
        float(weights.get("audioPeak", 0.28)) * audio_peak
        + float(weights.get("keyword", 0.22)) * keyword
        + float(weights.get("semanticPhrase", 0.18)) * phrase
        + float(weights.get("chatBurst", 0.15)) * chat_burst
        + float(weights.get("scene", 0.08)) * scene
        + float(weights.get("ocr", 0.05)) * ocr
    )
    anti_pen = anti_kw * float(penalties.get("antiKeyword", 0.35))
    config_final = max(
        0.0, min(1.0, raw - dur_pen - weak_text_pen - dup_pen - anti_pen)
    )
    final = config_final
    if ranker is not None:
        ml_scores = ranker.predict_proba([features_to_vector(feats)])
        if ml_scores:
            ml = float(ml_scores[0])
            final = max(
                0.0,
                min(1.0, (1.0 - ranker_blend) * config_final + ranker_blend * ml),
            )

    peak_pos = feats.audio.get("peak_offset_seconds")
    bd = SignalBreakdown(
        audio_peak_score=audio_peak,
        keyword_score=keyword,
        semantic_phrase_score=phrase,
        chat_burst_score=chat_burst,
        scene_score=scene,
        ocr_score=ocr,
        duplicate_penalty=dup_pen,
        duration_penalty=dur_pen + weak_text_pen,
        final_score=final,
        matched_keywords=matched_kw,
        matched_phrases=matched_phr,
        audio_peak_position=float(peak_pos) if peak_pos is not None else None,
        duplicate_warning=duplicate_of,
    )
    bd.explanation = _build_explanation(bd)

    return ScoredCandidate(
        candidate=cand,
        breakdown=bd,
        title_suggestion=_title_from_candidate(cand, bd),
        features=feats.to_dict(),
    )


def score_candidates(
    candidates: list[ProfileCandidate],
    *,
    segments: list[Segment],
    audio: AudioFeatureSeries | None,
    chat: ChatDensitySeries | None,
    scene_cuts: list[float] | None,
    config: ProfileConfig,
    duration_seconds: float = 0.0,
    video_path: Path | None = None,
    vibe: str = "",
    ranker: RankerArtifact | None = None,
) -> list[ScoredCandidate]:
    """Score all candidates and mark near-duplicates."""
    scored: list[ScoredCandidate] = []
    for i, cand in enumerate(candidates):
        duplicate_of = False
        for j, other in enumerate(candidates):
            if i != j and cand.overlap_iou(other) >= config.dedupe_threshold():
                if sum(other.raw_scores.values()) > sum(cand.raw_scores.values()):
                    duplicate_of = True
                    break
        scored.append(
            score_candidate(
                cand,
                segments=segments,
                audio=audio,
                chat=chat,
                scene_cuts=scene_cuts,
                config=config,
                duration_seconds=duration_seconds,
                duplicate_of=duplicate_of,
                video_path=video_path,
                vibe=vibe,
                ranker=ranker,
            )
        )
    return sorted(scored, key=lambda s: -s.score)
