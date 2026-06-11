"""Semantic phrase embeddings via sentence-transformers."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import structlog

from ..config import get_settings

log = structlog.get_logger(__name__)

_MODEL = None
_MODEL_FAILED = False


def embeddings_enabled() -> bool:
    return get_settings().profile_embeddings_enabled


@lru_cache(maxsize=1)
def _load_model():
    global _MODEL, _MODEL_FAILED
    if _MODEL_FAILED:
        return None
    try:
        from sentence_transformers import SentenceTransformer

        name = get_settings().profile_embedding_model
        _MODEL = SentenceTransformer(name)
        log.info("embedding_model_loaded", model=name)
        return _MODEL
    except Exception as exc:
        _MODEL_FAILED = True
        log.warning("embedding_model_unavailable", error=str(exc))
        return None


def embed_texts(texts: list[str]) -> list[list[float]] | None:
    if not embeddings_enabled() or not texts:
        return None
    model = _load_model()
    if model is None:
        return None
    vectors = model.encode(texts, normalize_embeddings=True)
    return [v.tolist() for v in vectors]


def max_phrase_similarity(
    text: str,
    phrases: list[str],
    *,
    threshold: float = 0.62,
) -> tuple[float, list[str]]:
    """Return best cosine similarity to known highlight phrases."""
    if not text or not phrases or not embeddings_enabled():
        return 0.0, []

    query_vecs = embed_texts([text])
    phrase_vecs = embed_texts(phrases)
    if not query_vecs or not phrase_vecs:
        # Substring fallback
        lower = text.lower()
        matched = [p for p in phrases if p.lower() in lower]
        score = min(1.0, len(matched) / max(1, min(3, len(phrases) // 4 + 1)))
        return score, matched

    import numpy as np

    q = np.array(query_vecs[0])
    matched: list[str] = []
    best = 0.0
    for phrase, vec in zip(phrases, phrase_vecs, strict=False):
        sim = float(np.dot(q, np.array(vec)))
        if sim >= threshold:
            matched.append(phrase)
        best = max(best, sim)
    return min(1.0, best), matched
