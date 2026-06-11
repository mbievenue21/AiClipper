"""Per-window feature extraction for profile training and scoring."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import structlog

from ..analyze.audio_features import AudioFeatureSeries
from ..analyze.candidates import Segment
from ..analyze.chat_features import ChatDensitySeries
from ..analyze.signal_peaks import find_peak_indices
from .config import ProfileConfig
from .embeddings import max_phrase_similarity
from .ocr import ocr_window
from .vlm_features import motion_brightness_delta, vlm_highlight_score

log = structlog.get_logger(__name__)

_SHOUT_PATTERN = re.compile(
    r"\b(oh my god|omg|no way|what|insane|crazy|holy|let'?s go|ace|clutch|"
    r"one tap|four kill|he'?s one|last alive|spike planted|flawless)\b",
    re.IGNORECASE,
)
_QUESTION_EXCLAM = re.compile(r"[?!]")


@dataclass
class WindowFeatures:
    start_seconds: float
    end_seconds: float
    transcript_text: str = ""
    text_before: str = ""
    text_after: str = ""
    audio: dict[str, float] = field(default_factory=dict)
    transcript: dict[str, Any] = field(default_factory=dict)
    chat: dict[str, float] = field(default_factory=dict)
    visual: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_seconds": self.start_seconds,
            "end_seconds": self.end_seconds,
            "transcript_text": self.transcript_text,
            "text_before": self.text_before,
            "text_after": self.text_after,
            "audio": self.audio,
            "transcript": self.transcript,
            "chat": self.chat,
            "visual": self.visual,
            "metadata": self.metadata,
        }


def _text_for_range(
    segments: list[Segment],
    start: float,
    end: float,
) -> str:
    parts: list[str] = []
    for seg in segments:
        if seg.end_seconds < start or seg.start_seconds > end:
            continue
        parts.append(seg.text.strip())
    return " ".join(parts).strip()


def _keyword_hits(text: str, keywords: dict[str, float]) -> tuple[float, list[str]]:
    if not text or not keywords:
        return 0.0, []
    lower = text.lower()
    matched: list[str] = []
    score = 0.0
    for kw, weight in keywords.items():
        if kw.lower() in lower:
            matched.append(kw)
            score = max(score, float(weight))
    return min(1.0, score), matched


def _phrase_similarity(text: str, phrases: list[str]) -> tuple[float, list[str]]:
    if not text or not phrases:
        return 0.0, []
    lower = text.lower()
    matched: list[str] = []
    for phrase in phrases:
        if phrase.lower() in lower:
            matched.append(phrase)
    if not matched:
        return 0.0, []
    return min(1.0, len(matched) / max(1, min(3, len(phrases) // 4 + 1))), matched


def _audio_window_features(
    audio: AudioFeatureSeries,
    start: float,
    end: float,
    *,
    z_cap: float = 3.0,
) -> dict[str, float]:
    if end <= start or not audio.samples:
        return {}

    i = max(0, int(round(start)))
    j = min(len(audio.samples), int(round(end)) + 1)
    if j <= i:
        return {}

    slice_ = audio.samples[i:j]
    excitements = [s["excitement"] for s in slice_]
    rms_values = [s.get("rmsDb", -40.0) for s in slice_]

    peak_exc = max(excitements)
    mean_exc = float(np.mean(excitements))
    all_exc = [s["excitement"] for s in audio.samples]
    baseline_mean = float(np.mean(all_exc)) if all_exc else 0.0
    baseline_std = float(np.std(all_exc)) or 0.05
    peak_z = (peak_exc - baseline_mean) / baseline_std
    peak_z = max(0.0, min(z_cap, peak_z / z_cap))

    peak_idx = max(range(i, j), key=lambda k: audio.samples[k]["excitement"])
    peak_t = float(audio.samples[peak_idx]["t"])
    peak_offset = peak_t - start

    # Silence before/after peak within window
    peak_local = peak_idx - i
    before = excitements[:peak_local] if peak_local > 0 else []
    after = excitements[peak_local + 1 :] if peak_local < len(excitements) - 1 else []
    silence_before = 1.0 - (max(before) if before else 0.0)
    silence_after = 1.0 - (max(after) if after else 0.0)

    peaks_in_window = len(
        find_peak_indices(excitements, min_height=0.55, min_distance=3)
    )

    return {
        "rms_energy": float(np.mean(rms_values)),
        "normalized_loudness": mean_exc,
        "peak_amplitude": peak_exc,
        "peak_z_score": peak_z,
        "onset_strength": peak_exc - mean_exc,
        "silence_before_peak": silence_before,
        "silence_after_peak": silence_after,
        "peak_offset_seconds": peak_offset,
        "yell_like_energy": 1.0 if peak_exc >= 0.75 and peak_z >= 0.6 else 0.0,
        "burst_duration": float(sum(1 for e in excitements if e >= 0.6)),
        "peak_count": float(peaks_in_window),
    }


def _chat_window_features(
    chat: ChatDensitySeries,
    start: float,
    end: float,
    *,
    z_cap: float = 3.0,
) -> dict[str, float]:
    if end <= start or not chat.normalised:
        return {}

    i = max(0, int(round(start)))
    j = min(len(chat.normalised), int(round(end)) + 1)
    if j <= i:
        return {}

    slice_ = chat.normalised[i:j]
    peak = max(slice_)
    mean = float(np.mean(slice_))
    all_vals = chat.normalised
    baseline_mean = float(np.mean(all_vals)) if all_vals else 0.0
    baseline_std = float(np.std(all_vals)) or 0.05
    z = (peak - baseline_mean) / baseline_std
    z_norm = max(0.0, min(z_cap, z / z_cap))

    return {
        "messages_per_second": mean,
        "burst_z_score": z_norm,
        "burst_duration": float(sum(1 for v in slice_ if v >= 0.5)),
        "peak_density": peak,
    }


def extract_window_features(
    *,
    start_seconds: float,
    end_seconds: float,
    segments: list[Segment],
    audio: AudioFeatureSeries | None = None,
    chat: ChatDensitySeries | None = None,
    scene_cuts: list[float] | None = None,
    config: ProfileConfig | None = None,
    duration_seconds: float = 0.0,
    candidate_sources: list[str] | None = None,
    video_path: Path | None = None,
    vibe: str = "",
) -> WindowFeatures:
    """Extract explainable feature groups for a candidate window."""
    cfg = config or ProfileConfig()
    text = _text_for_range(segments, start_seconds, end_seconds)
    before = _text_for_range(segments, max(0, start_seconds - 15), start_seconds)
    after = _text_for_range(segments, end_seconds, end_seconds + 15)

    keyword_score, matched_kw = _keyword_hits(text, cfg.keywords)
    anti_score, matched_anti = _keyword_hits(text, cfg.anti_keywords)
    if cfg.candidate_sources.get("semanticPhrases", True):
        phrase_score, matched_phrases = max_phrase_similarity(
            text,
            cfg.phrases,
            threshold=float(cfg.thresholds.get("embeddingSimilarityMin", 0.62)),
        )
        if phrase_score <= 0:
            phrase_score, matched_phrases = _phrase_similarity(text, cfg.phrases)
    else:
        phrase_score, matched_phrases = _phrase_similarity(text, cfg.phrases)

    shout_hits = len(_SHOUT_PATTERN.findall(text))
    qe_density = len(_QUESTION_EXCLAM.findall(text)) / max(1, len(text.split()))

    audio_feats: dict[str, float] = {}
    if audio is not None:
        audio_feats = _audio_window_features(
            audio,
            start_seconds,
            end_seconds,
            z_cap=float(cfg.normalization.get("audioZScoreCap", 3.0)),
        )

    chat_feats: dict[str, float] = {}
    if chat is not None:
        chat_feats = _chat_window_features(
            chat,
            start_seconds,
            end_seconds,
            z_cap=float(cfg.normalization.get("chatZScoreCap", 3.0)),
        )

    scene_count = 0
    if scene_cuts:
        scene_count = sum(
            1 for c in scene_cuts if start_seconds <= c <= end_seconds
        )

    visual_extra = motion_brightness_delta(video_path, start_seconds, end_seconds)
    ocr_data: dict[str, Any] = {"ocr_score": 0.0, "ocr_terms": []}
    if cfg.candidate_sources.get("ocrEvents", False) and video_path is not None:
        ocr_data = ocr_window(video_path, start_seconds, end_seconds)

    vlm_score = 0.0
    if video_path is not None:
        vlm_score = vlm_highlight_score(
            video_path, start_seconds, end_seconds, vibe=vibe
        )

    duration = end_seconds - start_seconds
    rel_position = (
        (start_seconds + end_seconds) / 2 / duration_seconds
        if duration_seconds > 0
        else 0.5
    )

    return WindowFeatures(
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        transcript_text=text,
        text_before=before,
        text_after=after,
        audio=audio_feats,
        transcript={
            "keyword_score": keyword_score,
            "matched_keywords": matched_kw,
            "anti_keyword_score": anti_score,
            "matched_anti_keywords": matched_anti,
            "phrase_score": phrase_score,
            "matched_phrases": matched_phrases,
            "shout_hits": float(shout_hits),
            "question_exclamation_density": qe_density,
            "word_count": float(len(text.split())),
        },
        chat=chat_feats,
        visual={
            "scene_cut_count": float(scene_count),
            "scene_cut_bonus": float(cfg.thresholds.get("sceneCutBonus", 0.15))
            if scene_count > 0
            else 0.0,
            "motion_delta": float(visual_extra.get("motion_delta", 0.0)),
            "brightness_delta": float(visual_extra.get("brightness_delta", 0.0)),
            "ocr_score": float(ocr_data.get("ocr_score", 0.0)),
            "ocr_terms": ocr_data.get("ocr_terms", []),
            "vlm_score": float(vlm_score),
        },
        metadata={
            "duration_seconds": duration,
            "relative_position": rel_position,
            "candidate_sources": candidate_sources or [],
        },
    )
