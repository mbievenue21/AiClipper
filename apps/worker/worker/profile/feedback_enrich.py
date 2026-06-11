"""Gemini Flash enrichment for editor training notes."""

from __future__ import annotations

import json
import time
from typing import Any

import structlog

from ..config import get_settings

log = structlog.get_logger(__name__)

_ENRICH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "momentType": {"type": "string"},
        "highlightKeywords": {
            "type": "array",
            "items": {"type": "string"},
        },
        "antiKeywords": {"type": "array", "items": {"type": "string"}},
        "phrases": {"type": "array", "items": {"type": "string"}},
        "whyHighlight": {"type": "string"},
        "whyNotHighlight": {"type": "string"},
        "signalHints": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["highlightKeywords", "phrases", "signalHints"],
}


def enrich_editor_notes(
    *,
    label: str,
    editor_notes: dict[str, Any],
    transcript_excerpt: str = "",
    signal_summary: str = "",
    game: str = "",
) -> dict[str, Any] | None:
    """Use Gemini Flash to structure user notes into training signals."""
    try:
        from ..analyze.gemini import is_configured
        from google import genai
        from google.genai import types as genai_types
    except ImportError:
        return None

    if not is_configured():
        return None

    settings = get_settings()
    if not settings.gemini_api_key:
        return None

    positive = label in ("positive", "accepted", "published")
    user_kw = editor_notes.get("userKeywords") or []
    user_phr = editor_notes.get("userPhrases") or []
    user_rat = editor_notes.get("userRationale") or ""
    user_anti = editor_notes.get("userAntiKeywords") or []

    prompt = f"""You help train a gaming highlight detection profile.

Label: {"POSITIVE highlight" if positive else "NEGATIVE / not a highlight"}
Game/context: {game or "gaming stream / reaction shorts"}

User highlight keywords: {", ".join(user_kw) or "(none)"}
User key sentences:
{chr(10).join(f"- {p}" for p in user_phr) or "(none)"}
User rationale: {user_rat or "(none)"}
User anti-keywords: {", ".join(user_anti) or "(none)"}

Transcript excerpt (if any): {transcript_excerpt[:1200] or "(none)"}
Automated signal summary: {signal_summary[:600] or "(none)"}

Return JSON only. Extract:
- momentType: short slug (e.g. clutch_ace, funny_fail, dead_air)
- highlightKeywords: terms that SHOULD boost scoring (lowercase)
- antiKeywords: terms that should DOWN-RANK similar windows (lowercase)
- phrases: 1-5 short spoken phrases worth semantic matching
- whyHighlight / whyNotHighlight: one sentence each (empty if N/A)
- signalHints: subset of [audio_peak, chat_burst, keyword, phrase, scene, reaction, dead_air]
"""

    try:
        client = genai.Client(api_key=settings.gemini_api_key)
        resp = client.models.generate_content(
            model=settings.gemini_flash_model,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_ENRICH_SCHEMA,
                max_output_tokens=1024,
            ),
        )
        raw = resp.text or "{}"
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        data["enrichedAt"] = int(time.time() * 1000)
        return data
    except Exception as exc:
        log.warning("editor_notes_enrich_failed", error=str(exc)[:200])
        return None
