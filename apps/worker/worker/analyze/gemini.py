"""Gemini 2.5 Flash rerank for highlight candidates.

We send a compact prompt: the user's vibe hint, the desired count and clip
length range, and a numbered list of locally-scored candidates with their
transcript text. The model returns structured JSON: which candidates to
keep, a snappy title, a 1–2 sentence "why this is interesting" summary,
and a small set of reason tags.

If ``GEMINI_API_KEY`` is missing, this module returns ``None`` and the
caller falls back to local scoring with auto-generated titles.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import structlog

from ..config import get_settings
from .candidates import Candidate

log = structlog.get_logger(__name__)

GEMINI_MODEL = "gemini-2.5-flash"

_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "highlights": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "candidate_index": {"type": "integer"},
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "llm_score": {"type": "number"},
                    "reason_tags": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["candidate_index", "title", "summary", "llm_score"],
                "propertyOrdering": [
                    "candidate_index",
                    "title",
                    "summary",
                    "llm_score",
                    "reason_tags",
                ],
            },
        },
    },
    "required": ["highlights"],
}


@dataclass
class LlmPick:
    candidate_index: int
    title: str
    summary: str
    llm_score: float
    reason_tags: list[str]


def is_configured() -> bool:
    return bool(get_settings().gemini_api_key)


def rerank_with_gemini(
    candidates: list[Candidate],
    *,
    top_n: int,
    vibe: str,
    language: str | None,
) -> list[LlmPick] | None:
    """Ask Gemini to pick the best ``top_n`` and write titles/summaries.

    Returns ``None`` if Gemini is not configured or the call fails. The
    caller is responsible for falling back to local scoring in that case.
    """
    settings = get_settings()
    if not settings.gemini_api_key:
        log.info("gemini_skipped_no_api_key")
        return None
    if not candidates:
        return []

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        log.warning("gemini_import_failed", error=str(exc))
        return None

    prompt = _build_prompt(candidates, top_n=top_n, vibe=vibe, language=language)

    try:
        client = genai.Client(api_key=settings.gemini_api_key)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_RESPONSE_SCHEMA,
                temperature=0.4,
                # We want a quick, decisive answer — disable extended thinking.
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
    except Exception as exc:
        log.warning("gemini_call_failed", error=str(exc))
        return None

    text = (response.text or "").strip()
    if not text:
        log.warning("gemini_empty_response")
        return None

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        log.warning("gemini_parse_failed", error=str(exc), raw=text[:500])
        return None

    raw_picks = parsed.get("highlights") or []
    picks: list[LlmPick] = []
    for item in raw_picks:
        try:
            idx = int(item["candidate_index"])
            if not 0 <= idx < len(candidates):
                continue
            picks.append(
                LlmPick(
                    candidate_index=idx,
                    title=str(item.get("title") or "").strip()[:120],
                    summary=str(item.get("summary") or "").strip()[:500],
                    llm_score=max(0.0, min(1.0, float(item.get("llm_score", 0.5)))),
                    reason_tags=[
                        str(t).strip()[:32]
                        for t in (item.get("reason_tags") or [])
                        if str(t).strip()
                    ][:5],
                )
            )
        except (KeyError, ValueError, TypeError):
            continue

    log.info("gemini_rerank_done", returned=len(picks), requested=top_n)
    return picks[:top_n]


def _build_prompt(
    candidates: list[Candidate], *, top_n: int, vibe: str, language: str | None
) -> str:
    lines: list[str] = []
    lines.append(
        f"You are picking the {top_n} most clip-worthy moments from a long-form "
        f"video. Each candidate is a slice of the transcript that already scored "
        f"high on audio energy / chat reaction / keywords."
    )
    if vibe.strip():
        lines.append(f'User vibe hint: "{vibe.strip()}".')
    if language:
        lines.append(f"Source language: {language}.")
    lines.append(
        "Pick the candidates that would work best as standalone short-form clips: "
        "a clear hook, a satisfying payoff, and self-contained context. Avoid "
        "mid-thought cuts and avoid picking two candidates that cover the same idea."
    )
    lines.append(
        "For each pick, write a punchy <= 80-character title, a 1–2 sentence "
        "summary of WHY the moment lands, and 1–5 short reason tags "
        '(e.g. "laughter", "key insight", "twist", "audience reaction").'
    )
    lines.append(
        "Score each pick 0..1 where 1.0 means \"definitely use this clip\" and "
        "0.3 means \"weak — skip if you have better\"."
    )
    lines.append("")
    lines.append("CANDIDATES:")
    for i, c in enumerate(candidates):
        # Cap text length so the prompt stays bounded for long VODs.
        body = c.text[:600] + ("…" if len(c.text) > 600 else "")
        lines.append(
            f"[{i}] {c.start_seconds:.1f}s–{c.end_seconds:.1f}s "
            f"(dur {c.duration_seconds:.1f}s, local_score {c.composite_score:.2f}, "
            f"audio {c.audio_score:.2f}, chat {c.chat_score:.2f}, kw {c.keyword_score:.2f})\n"
            f"    \"{body}\""
        )
    lines.append("")
    lines.append(
        f"Return JSON matching the schema with at most {top_n} highlights, "
        f"ordered best-first."
    )
    return "\n".join(lines)
