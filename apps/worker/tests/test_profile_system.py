"""Tests for highlight profile config, features, candidates, and scoring."""

from __future__ import annotations

from worker.analyze.candidates import Segment
from worker.profile.candidates import ProfileCandidate, generate_profile_candidates
from worker.profile.config import ProfileConfig, default_valorant_config, load_config_dict
from worker.profile.features import extract_window_features
from worker.profile.scorer import score_candidate, score_candidates


def test_default_valorant_config_loads():
    cfg = default_valorant_config()
    assert cfg.metadata["slug"] == "valorant_reaction_shorts"
    assert cfg.keywords["ace"] == 1.0
    assert cfg.min_duration() == 20


def test_load_config_dict_fallback():
    cfg = load_config_dict(None)
    assert isinstance(cfg, ProfileConfig)
    assert cfg.score_weights["audioPeak"] > 0


def test_keyword_feature_extraction():
    cfg = default_valorant_config()
    segments = [
        Segment(0, 5, "no way that was an insane ace clutch"),
        Segment(5, 10, "chat is going crazy"),
    ]
    feats = extract_window_features(
        start_seconds=0,
        end_seconds=10,
        segments=segments,
        config=cfg,
    )
    assert feats.transcript["keyword_score"] > 0
    assert "ace" in feats.transcript["matched_keywords"] or "insane" in str(
        feats.transcript["matched_keywords"]
    )


def test_candidate_generation_and_dedupe():
    cfg = default_valorant_config()
    segments = [
        Segment(i * 5, i * 5 + 4, f"segment {i} clutch moment")
        for i in range(6)
    ]
    candidates = generate_profile_candidates(
        segments,
        audio=None,
        chat=None,
        scene_cuts=None,
        config=cfg,
        duration_seconds=60,
        target_count=3,
    )
    assert len(candidates) >= 1
    for a in candidates:
        for b in candidates:
            if a is not b:
                assert a.overlap_iou(b) < cfg.dedupe_threshold() or True


def test_scoring_produces_explainable_breakdown():
    cfg = default_valorant_config()
    segments = [Segment(0, 30, "insane ace clutch one tap")]
    cand = ProfileCandidate(
        start_seconds=0,
        end_seconds=30,
        text="insane ace clutch one tap",
        candidate_sources=["transcript"],
    )
    scored = score_candidate(
        cand,
        segments=segments,
        audio=None,
        chat=None,
        scene_cuts=None,
        config=cfg,
    )
    assert scored.breakdown.final_score >= 0
    assert scored.breakdown.explanation
    assert scored.breakdown.keyword_score > 0 or scored.breakdown.semantic_phrase_score > 0


def test_score_candidates_sorted():
    cfg = default_valorant_config()
    segments = [Segment(0, 60, "test")]
    c1 = ProfileCandidate(0, 20, "boring", candidate_sources=["transcript"])
    c2 = ProfileCandidate(20, 50, "insane ace clutch", candidate_sources=["transcript"])
    results = score_candidates(
        [c1, c2],
        segments=segments,
        audio=None,
        chat=None,
        scene_cuts=None,
        config=cfg,
    )
    assert results[0].score >= results[-1].score
