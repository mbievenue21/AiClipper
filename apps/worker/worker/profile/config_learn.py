"""Merge editor + Gemini notes into profile config before Optuna."""

from __future__ import annotations

import copy
from typing import Any

from .config import ProfileConfig


def _label_positive(label: str) -> bool:
    return label in ("positive", "accepted", "published")


def merge_editor_notes_into_config(
    base: ProfileConfig,
    examples: list[Any],
) -> ProfileConfig:
    """Seed keywords/phrases/anti-keywords from labeled editor feedback."""
    cfg = copy.deepcopy(base)
    keywords = dict(cfg.keywords)
    anti = dict(cfg.anti_keywords)
    phrases = list(cfg.phrases)

    for ex in examples:
        features = getattr(ex, "features", None)
        if not isinstance(features, dict):
            continue
        notes = features.get("editorNotes")
        if not isinstance(notes, dict):
            continue

        label = str(getattr(ex, "label", ""))
        positive = _label_positive(label)
        gemini = notes.get("gemini") if isinstance(notes.get("gemini"), dict) else {}

        boost_kw = list(notes.get("userKeywords") or []) + list(
            gemini.get("highlightKeywords") or []
        )
        demote_kw = list(notes.get("userAntiKeywords") or []) + list(
            gemini.get("antiKeywords") or []
        )
        new_phrases = list(notes.get("userPhrases") or []) + list(
            gemini.get("phrases") or []
        )

        if positive:
            for kw in boost_kw:
                k = str(kw).strip().lower()
                if not k:
                    continue
                keywords[k] = min(1.0, keywords.get(k, 0.55) + 0.08)
            for kw in demote_kw:
                k = str(kw).strip().lower()
                if k in keywords:
                    keywords[k] = max(0.15, keywords[k] - 0.12)
        else:
            for kw in demote_kw + boost_kw:
                k = str(kw).strip().lower()
                if not k:
                    continue
                anti[k] = min(1.0, anti.get(k, 0.5) + 0.1)
                if k in keywords:
                    keywords[k] = max(0.1, keywords[k] - 0.15)

        for phrase in new_phrases:
            p = str(phrase).strip()
            if p and p not in phrases:
                phrases.append(p)

    cfg.keywords = keywords
    cfg.anti_keywords = anti
    cfg.phrases = phrases[:40]
    return cfg
