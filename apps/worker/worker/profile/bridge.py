"""Bridge profile-scored candidates into the legacy Candidate type."""

from __future__ import annotations

import json
from typing import Any

from ..analyze.candidates import Candidate, Segment, _transcript_for_window
from ..models import ProfileScoredCandidate


def scored_rows_to_candidates(
    rows: list[ProfileScoredCandidate],
    segments: list[Segment],
) -> list[Candidate]:
    out: list[Candidate] = []
    for row in rows:
        text, idxs = _transcript_for_window(
            segments, float(row.start_seconds), float(row.end_seconds)
        )
        breakdown: dict[str, Any] = {}
        if row.signal_breakdown_json:
            try:
                breakdown = json.loads(row.signal_breakdown_json)
            except json.JSONDecodeError:
                breakdown = {}

        audio_score = float(breakdown.get("audioPeakScore", 0.0))
        keyword_score = float(breakdown.get("keywordScore", 0.0))
        chat_score = float(breakdown.get("chatBurstScore", 0.0))
        phrase_score = float(breakdown.get("semanticPhraseScore", 0.0))
        composite = float(row.score)

        reason_json = {
            "profileScore": {
                "audioPeakScore": float(breakdown.get("audioPeakScore", 0.0)),
                "keywordScore": float(breakdown.get("keywordScore", 0.0)),
                "semanticPhraseScore": float(breakdown.get("semanticPhraseScore", 0.0)),
                "chatBurstScore": float(breakdown.get("chatBurstScore", 0.0)),
                "sceneScore": float(breakdown.get("sceneScore", 0.0)),
                "ocrScore": float(breakdown.get("ocrScore", 0.0)),
                "duplicatePenalty": float(breakdown.get("duplicatePenalty", 0.0)),
                "durationPenalty": float(breakdown.get("durationPenalty", 0.0)),
                "finalScore": float(breakdown.get("finalScore", row.score)),
                "explanation": breakdown.get("explanation") or row.explanation,
            },
            "profileVersionId": row.profile_version_id,
            "matchedKeywords": breakdown.get("matchedKeywords", []),
            "matchedPhrases": breakdown.get("matchedPhrases", []),
            "audioPeakPosition": breakdown.get("audioPeakPosition"),
            "duplicateWarning": breakdown.get("duplicateWarning", False),
            "scores": {
                "audio": audio_score,
                "chat": chat_score,
                "keyword": keyword_score,
                "phrase": phrase_score,
                "profile": composite,
            },
        }

        out.append(
            Candidate(
                start_seconds=float(row.start_seconds),
                end_seconds=float(row.end_seconds),
                text=text,
                audio_score=audio_score,
                chat_score=chat_score,
                keyword_score=max(keyword_score, phrase_score),
                composite_score=composite,
                segment_indices=idxs,
                seed_source="profile",
                fusion_score=composite,
                confidence=composite,
                sources=["profile_score"],
                reason_json=reason_json,
            )
        )
    return sorted(out, key=lambda c: -c.composite_score)
