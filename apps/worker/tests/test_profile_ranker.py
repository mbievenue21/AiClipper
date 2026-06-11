"""Tests for LightGBM ranker training."""

from __future__ import annotations

from worker.profile.features import WindowFeatures, extract_window_features
from worker.profile.config import default_valorant_config
from worker.profile.ranker import features_to_vector, train_ranker
from worker.analyze.candidates import Segment


def test_features_to_vector_shape():
    feats = extract_window_features(
        start_seconds=0,
        end_seconds=30,
        segments=[Segment(0, 30, "insane ace")],
        config=default_valorant_config(),
    )
    vec = features_to_vector(feats)
    assert len(vec) == 11
    assert all(isinstance(v, float) for v in vec)


def test_train_ranker_skips_small_dataset():
    cfg = default_valorant_config()
    rows = [
        (
            extract_window_features(
                start_seconds=0,
                end_seconds=20,
                segments=[Segment(0, 20, "ace")],
                config=cfg,
            ),
            1,
        )
    ]
    assert train_ranker(rows, profile_id="test", version_number=1) is None
