"""Profile config JSON schema and defaults."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProfileConfig:
    metadata: dict[str, Any] = field(default_factory=dict)
    candidate_sources: dict[str, bool] = field(default_factory=dict)
    timing: dict[str, float] = field(default_factory=dict)
    keywords: dict[str, float] = field(default_factory=dict)
    anti_keywords: dict[str, float] = field(default_factory=dict)
    phrases: list[str] = field(default_factory=list)
    score_weights: dict[str, float] = field(default_factory=dict)
    thresholds: dict[str, float] = field(default_factory=dict)
    penalties: dict[str, float] = field(default_factory=dict)
    normalization: dict[str, float] = field(default_factory=dict)
    render_defaults: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProfileConfig:
        return cls(
            metadata=dict(data.get("metadata") or {}),
            candidate_sources=dict(data.get("candidateSources") or {}),
            timing=dict(data.get("timing") or {}),
            keywords=dict(data.get("keywords") or {}),
            anti_keywords=dict(data.get("antiKeywords") or {}),
            phrases=list(data.get("phrases") or []),
            score_weights=dict(data.get("scoreWeights") or {}),
            thresholds=dict(data.get("thresholds") or {}),
            penalties=dict(data.get("penalties") or {}),
            normalization=dict(data.get("normalization") or {}),
            render_defaults=dict(data.get("renderDefaults") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "metadata": self.metadata,
            "candidateSources": self.candidate_sources,
            "timing": self.timing,
            "keywords": self.keywords,
            "antiKeywords": self.anti_keywords,
            "phrases": self.phrases,
            "scoreWeights": self.score_weights,
            "thresholds": self.thresholds,
            "penalties": self.penalties,
            "normalization": self.normalization,
            "renderDefaults": self.render_defaults,
        }

    def min_duration(self) -> float:
        return float(self.timing.get("minDurationSeconds", 20))

    def max_duration(self) -> float:
        return float(self.timing.get("maxDurationSeconds", 60))

    def target_duration(self) -> float:
        return float(self.timing.get("targetDurationSeconds", 45))

    def merge_window(self) -> float:
        return float(self.timing.get("mergeWindowSeconds", 12))

    def dedupe_threshold(self) -> float:
        return float(self.timing.get("dedupeOverlapThreshold", 0.55))


def default_valorant_config() -> ProfileConfig:
    return ProfileConfig.from_dict(
        {
            "metadata": {
                "name": "Valorant Reaction Shorts",
                "slug": "valorant_reaction_shorts",
                "game": "valorant",
                "contentType": "reaction_shorts",
            },
            "candidateSources": {
                "audioPeaks": True,
                "transcriptKeywords": True,
                "semanticPhrases": True,
                "chatBursts": True,
                "sceneCuts": True,
                "ocrEvents": False,
            },
            "timing": {
                "minDurationSeconds": 20,
                "targetDurationSeconds": 45,
                "maxDurationSeconds": 60,
                "preRollSeconds": 8,
                "postRollSeconds": 2,
                "mergeWindowSeconds": 12,
                "dedupeOverlapThreshold": 0.55,
            },
            "keywords": {
                "ace": 1.0,
                "clutch": 0.95,
                "one tap": 0.9,
                "four kill": 0.9,
                "quad": 0.85,
                "insane": 0.8,
                "no way": 0.85,
                "what": 0.6,
                "flawless": 0.85,
                "spike": 0.7,
                "planted": 0.65,
                "defuse": 0.7,
                "last alive": 0.9,
                "he's one": 0.85,
                "team ace": 0.95,
            },
            "phrases": [
                "no way",
                "what",
                "insane",
                "ace",
                "clutch",
                "one tap",
                "four kill",
                "he's one",
                "last alive",
                "spike planted",
                "flawless",
                "team ace",
                "let's go",
                "holy",
            ],
            "scoreWeights": {
                "audioPeak": 0.28,
                "keyword": 0.22,
                "semanticPhrase": 0.18,
                "chatBurst": 0.15,
                "scene": 0.08,
                "ocr": 0.05,
            },
            "thresholds": {
                "audioPeakMin": 0.55,
                "chatBurstMin": 0.5,
                "embeddingSimilarityMin": 0.62,
                "sceneCutBonus": 0.15,
            },
            "penalties": {
                "duplicate": 0.25,
                "tooShort": 0.3,
                "tooLong": 0.2,
                "weakTranscript": 0.15,
                "antiKeyword": 0.35,
            },
            "normalization": {
                "audioZScoreCap": 3.0,
                "chatZScoreCap": 3.0,
            },
            "renderDefaults": {"aspect": "9:16", "preRollSeconds": 8},
        }
    )


def load_config_dict(raw: dict[str, Any] | str | None) -> ProfileConfig:
    if raw is None:
        return default_valorant_config()
    if isinstance(raw, str):
        import json

        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return default_valorant_config()
    if not isinstance(raw, dict):
        return default_valorant_config()
    return ProfileConfig.from_dict(raw)
