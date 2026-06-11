"""Optuna-based profile config optimization."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

import structlog

from .config import ProfileConfig
from .scorer import score_candidate
from ..analyze.candidates import Segment
from ..analyze.audio_features import AudioFeatureSeries
from ..analyze.chat_features import ChatDensitySeries
from .candidates import ProfileCandidate

log = structlog.get_logger(__name__)


@dataclass
class TrainingExample:
    start_seconds: float
    end_seconds: float
    label: str
    features: dict[str, Any] | None = None


@dataclass
class OptimizationResult:
    config: ProfileConfig
    metrics: dict[str, float]
    trial_count: int


def _temporal_iou(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    overlap = max(0.0, min(a_end, b_end) - max(a_start, b_start))
    union = max(a_end, b_end) - min(a_start, b_start)
    return overlap / union if union > 0 else 0.0


def _evaluate_config(
    config: ProfileConfig,
    positives: list[TrainingExample],
    negatives: list[TrainingExample],
    segments: list[Segment],
    audio: AudioFeatureSeries | None,
    chat: ChatDensitySeries | None,
    *,
    k: int = 5,
) -> dict[str, float]:
    """Score config against labeled examples without full candidate generation."""
    pos_scores: list[float] = []
    for ex in positives:
        cand = ProfileCandidate(
            start_seconds=ex.start_seconds,
            end_seconds=ex.end_seconds,
            text="",
            candidate_sources=["training"],
        )
        scored = score_candidate(
            cand,
            segments=segments,
            audio=audio,
            chat=chat,
            scene_cuts=None,
            config=config,
        )
        pos_scores.append(scored.score)

    neg_scores: list[float] = []
    for ex in negatives:
        cand = ProfileCandidate(
            start_seconds=ex.start_seconds,
            end_seconds=ex.end_seconds,
            text="",
            candidate_sources=["training"],
        )
        scored = score_candidate(
            cand,
            segments=segments,
            audio=audio,
            chat=chat,
            scene_cuts=None,
            config=config,
        )
        neg_scores.append(scored.score)

    pos_scores.sort(reverse=True)
    recall_at_k = (
        sum(1 for s in pos_scores[:k] if s >= 0.45) / max(1, len(positives))
        if positives
        else 0.0
    )
    precision_at_k = 0.0
    if negatives:
        top = sorted(pos_scores + neg_scores, reverse=True)[:k]
        precision_at_k = sum(1 for s in top if s in pos_scores[:k]) / k

    mean_pos = sum(pos_scores) / max(1, len(pos_scores))
    mean_neg = sum(neg_scores) / max(1, len(neg_scores)) if neg_scores else 0.0

    return {
        "recallAtK": recall_at_k,
        "precisionAtK": precision_at_k,
        "meanPositiveScore": mean_pos,
        "meanNegativeScore": mean_neg,
        "separation": mean_pos - mean_neg,
    }


def optimize_profile_config(
    base_config: ProfileConfig,
    positives: list[TrainingExample],
    negatives: list[TrainingExample],
    segments: list[Segment],
    audio: AudioFeatureSeries | None = None,
    chat: ChatDensitySeries | None = None,
    *,
    n_trials: int = 40,
) -> OptimizationResult:
    """Tune score weights and thresholds with Optuna."""
    try:
        import optuna

        optuna.logging.set_verbosity(optuna.logging.WARNING)
    except ImportError:
        log.warning("optuna_not_installed_using_base_config")
        metrics = _evaluate_config(
            base_config, positives, negatives, segments, audio, chat
        )
        return OptimizationResult(
            config=base_config,
            metrics=metrics,
            trial_count=0,
        )

    def objective(trial: Any) -> float:
        cfg = copy.deepcopy(base_config)
        cfg.score_weights = {
            "audioPeak": trial.suggest_float("w_audio", 0.1, 0.45),
            "keyword": trial.suggest_float("w_keyword", 0.1, 0.35),
            "semanticPhrase": trial.suggest_float("w_phrase", 0.05, 0.3),
            "chatBurst": trial.suggest_float("w_chat", 0.05, 0.35),
            "scene": trial.suggest_float("w_scene", 0.02, 0.2),
            "ocr": 0.05,
        }
        total = sum(cfg.score_weights.values())
        if total > 0:
            for key in cfg.score_weights:
                cfg.score_weights[key] /= total

        cfg.thresholds["audioPeakMin"] = trial.suggest_float("audio_min", 0.4, 0.75)
        cfg.thresholds["chatBurstMin"] = trial.suggest_float("chat_min", 0.35, 0.7)
        cfg.penalties["duplicate"] = trial.suggest_float("dup_pen", 0.1, 0.4)
        cfg.penalties["tooShort"] = trial.suggest_float("short_pen", 0.1, 0.4)

        metrics = _evaluate_config(
            cfg, positives, negatives, segments, audio, chat
        )
        return -(metrics["recallAtK"] * 0.5 + metrics["separation"] * 0.5)

    study = optuna.create_study(direction="minimize")
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)

    best = copy.deepcopy(base_config)
    bp = study.best_params
    best.score_weights = {
        "audioPeak": bp["w_audio"],
        "keyword": bp["w_keyword"],
        "semanticPhrase": bp["w_phrase"],
        "chatBurst": bp["w_chat"],
        "scene": bp["w_scene"],
        "ocr": 0.05,
    }
    total = sum(best.score_weights.values())
    if total > 0:
        for key in best.score_weights:
            best.score_weights[key] /= total
    best.thresholds["audioPeakMin"] = bp["audio_min"]
    best.thresholds["chatBurstMin"] = bp["chat_min"]
    best.penalties["duplicate"] = bp["dup_pen"]
    best.penalties["tooShort"] = bp["short_pen"]

    metrics = _evaluate_config(best, positives, negatives, segments, audio, chat)
    metrics["bestObjective"] = float(-study.best_value)
    metrics["trialCount"] = len(study.trials)

    return OptimizationResult(
        config=best,
        metrics=metrics,
        trial_count=len(study.trials),
    )
