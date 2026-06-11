"""Tests for merging editor notes into profile config."""

from __future__ import annotations

from types import SimpleNamespace

from worker.profile.config import default_valorant_config
from worker.profile.config_learn import merge_editor_notes_into_config


def test_merge_positive_editor_keywords():
    base = default_valorant_config()
    examples = [
        SimpleNamespace(
            label="accepted",
            features={
                "editorNotes": {
                    "userKeywords": ["radiant", "collat"],
                    "userPhrases": ["no way that collat"],
                    "gemini": {
                        "highlightKeywords": ["ace"],
                        "phrases": ["chat is popping"],
                    },
                }
            },
        )
    ]
    merged = merge_editor_notes_into_config(base, examples)
    assert merged.keywords["radiant"] > 0
    assert merged.keywords["ace"] > 0
    assert "no way that collat" in merged.phrases


def test_merge_negative_adds_anti_keywords():
    base = default_valorant_config()
    examples = [
        SimpleNamespace(
            label="rejected",
            features={
                "editorNotes": {
                    "userAntiKeywords": ["ads", "setup"],
                    "userRationale": "just walking, no action",
                }
            },
        )
    ]
    merged = merge_editor_notes_into_config(base, examples)
    assert merged.anti_keywords.get("ads", 0) > 0
