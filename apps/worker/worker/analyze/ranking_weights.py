"""Load and apply user-learned ranking weights from ranking_preferences."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from ..db import session_scope
from ..models import RankingPreferences

DEFAULT_WEIGHTS: dict[str, float] = {
    "fusionVisual": 0.24,
    "fusionChat": 0.18,
    "fusionAudio": 0.16,
    "fusionTranscript": 0.14,
    "fusionAlignment": 0.10,
    "fusionScene": 0.10,
    "fusionAgreement": 0.08,
    "candidateAudio": 0.75,
    "candidateChat": 0.40,
    "candidateKeyword": 0.25,
    "candidateChatAudio": 0.45,
    "geminiBlendLlm": 0.55,
    "geminiBlendLocal": 0.45,
}


@dataclass
class RankingWeights:
    fusion_visual: float = 0.24
    fusion_chat: float = 0.18
    fusion_audio: float = 0.16
    fusion_transcript: float = 0.14
    fusion_alignment: float = 0.10
    fusion_scene: float = 0.10
    fusion_agreement: float = 0.08
    candidate_audio: float = 0.75
    candidate_chat: float = 0.40
    candidate_keyword: float = 0.25
    candidate_chat_audio: float = 0.45
    gemini_blend_llm: float = 0.55
    gemini_blend_local: float = 0.45
    learned_pre_roll_seconds: float = 8.0
    learned_tail_padding_seconds: float = 2.0
    editor_pad_before_seconds: float = 10.0
    editor_pad_after_seconds: float = 10.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RankingWeights:
        w = {**DEFAULT_WEIGHTS, **(data.get("weights") or data)}
        return cls(
            fusion_visual=float(w.get("fusionVisual", 0.24)),
            fusion_chat=float(w.get("fusionChat", 0.18)),
            fusion_audio=float(w.get("fusionAudio", 0.16)),
            fusion_transcript=float(w.get("fusionTranscript", 0.14)),
            fusion_alignment=float(w.get("fusionAlignment", 0.10)),
            fusion_scene=float(w.get("fusionScene", 0.10)),
            fusion_agreement=float(w.get("fusionAgreement", 0.08)),
            candidate_audio=float(w.get("candidateAudio", 0.75)),
            candidate_chat=float(w.get("candidateChat", 0.40)),
            candidate_keyword=float(w.get("candidateKeyword", 0.25)),
            candidate_chat_audio=float(w.get("candidateChatAudio", 0.45)),
            gemini_blend_llm=float(w.get("geminiBlendLlm", 0.55)),
            gemini_blend_local=float(w.get("geminiBlendLocal", 0.45)),
            learned_pre_roll_seconds=float(data.get("learned_pre_roll_seconds", 8.0)),
            learned_tail_padding_seconds=float(
                data.get("learned_tail_padding_seconds", 2.0)
            ),
            editor_pad_before_seconds=float(
                data.get("editor_pad_before_seconds", 10.0)
            ),
            editor_pad_after_seconds=float(data.get("editor_pad_after_seconds", 10.0)),
        )


def load_ranking_weights() -> RankingWeights:
    """Best-effort load of singleton ranking preferences."""
    try:
        with session_scope() as session:
            row = session.get(RankingPreferences, "default")
            if row is None:
                return RankingWeights()
            return RankingWeights.from_dict(
                {
                    "weights": row.weights,
                    "learned_pre_roll_seconds": row.learned_pre_roll_seconds,
                    "learned_tail_padding_seconds": row.learned_tail_padding_seconds,
                    "editor_pad_before_seconds": row.editor_pad_before_seconds,
                    "editor_pad_after_seconds": row.editor_pad_after_seconds,
                }
            )
    except Exception:
        return RankingWeights()
