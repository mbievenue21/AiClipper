"""LightGBM supervised ranker for profile scoring."""

from __future__ import annotations

import json
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from ..config import get_settings
from .features import WindowFeatures

log = structlog.get_logger(__name__)


FEATURE_KEYS = [
    "audio_peak_z",
    "audio_loudness",
    "audio_peak_count",
    "keyword_score",
    "phrase_score",
    "chat_burst_z",
    "scene_cut_count",
    "ocr_score",
    "duration_seconds",
    "word_count",
    "relative_position",
]


def ranker_enabled() -> bool:
    return get_settings().profile_ranker_enabled


def features_to_vector(feats: WindowFeatures) -> list[float]:
    return [
        float(feats.audio.get("peak_z_score", 0.0)),
        float(feats.audio.get("normalized_loudness", 0.0)),
        float(feats.audio.get("peak_count", 0.0)),
        float(feats.transcript.get("keyword_score", 0.0)),
        float(feats.transcript.get("phrase_score", 0.0)),
        float(feats.chat.get("burst_z_score", 0.0)),
        float(feats.visual.get("scene_cut_count", 0.0)),
        float(feats.visual.get("ocr_score", 0.0)),
        float(feats.metadata.get("duration_seconds", 0.0)) / 60.0,
        float(feats.transcript.get("word_count", 0.0)) / 50.0,
        float(feats.metadata.get("relative_position", 0.5)),
    ]


@dataclass
class RankerArtifact:
    model_type: str
    path: Path

    def predict_proba(self, vectors: list[list[float]]) -> list[float]:
        if not vectors:
            return []
        if self.model_type == "lightgbm_ranker":
            import lightgbm as lgb

            model = lgb.Booster(model_file=str(self.path))
            preds = model.predict(vectors)
            return [float(p) for p in preds]
        if self.model_type == "sklearn_ranker":
            with self.path.open("rb") as f:
                model = pickle.load(f)
            if hasattr(model, "predict_proba"):
                return [float(p[1]) for p in model.predict_proba(vectors)]
            return [float(p) for p in model.predict(vectors)]
        return [0.5 for _ in vectors]


def train_ranker(
    examples: list[tuple[WindowFeatures, int]],
    *,
    profile_id: str,
    version_number: int | None = None,
    artifact_key: str = "active",
    model_type: str = "lightgbm_ranker",
) -> RankerArtifact | None:
    if not ranker_enabled() or len(examples) < 8:
        return None

    pos = sum(1 for _, y in examples if y == 1)
    neg = len(examples) - pos
    if pos < 2 or neg < 2:
        log.info("ranker_skipped_imbalance", pos=pos, neg=neg)
        return None

    x = [features_to_vector(f) for f, _ in examples]
    y = [label for _, label in examples]

    out_dir = get_settings().media_root_path / "profiles" / profile_id / "models"
    out_dir.mkdir(parents=True, exist_ok=True)

    if model_type == "lightgbm_ranker":
        try:
            import lightgbm as lgb
            import numpy as np

            train = lgb.Dataset(np.array(x), label=y)
            params = {
                "objective": "binary",
                "metric": "auc",
                "verbosity": -1,
                "num_leaves": 15,
                "learning_rate": 0.08,
                "feature_fraction": 0.9,
            }
            model = lgb.train(params, train, num_boost_round=60)
            suffix = artifact_key or f"v{version_number or 1}"
            path = out_dir / f"ranker_{suffix}.txt"
            model.save_model(str(path))
            return RankerArtifact(model_type=model_type, path=path)
        except Exception as exc:
            log.warning("lightgbm_train_failed", error=str(exc))

    try:
        from sklearn.ensemble import GradientBoostingClassifier

        model = GradientBoostingClassifier(n_estimators=50, max_depth=3)
        model.fit(x, y)
        suffix = artifact_key or f"v{version_number or 1}"
        path = out_dir / f"ranker_sklearn_{suffix}.pkl"
        with path.open("wb") as f:
            pickle.dump(model, f)
        return RankerArtifact(model_type="sklearn_ranker", path=path)
    except Exception as exc:
        log.warning("sklearn_ranker_failed", error=str(exc))
        return None


def load_ranker(model_type: str | None, artifact_path: str | None) -> RankerArtifact | None:
    if not artifact_path or not model_type or model_type == "config_only":
        return None
    path = Path(artifact_path)
    if not path.is_absolute():
        path = (get_settings().media_root_path / artifact_path).resolve()
    if not path.exists():
        return None
    return RankerArtifact(model_type=model_type, path=path)
