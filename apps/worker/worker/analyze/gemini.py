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

DEFAULT_MODEL = "gemini-2.5-pro"
FLASH_MODEL = "gemini-2.5-flash"

# Pro reasoning needs more headroom than flash. We send ~10–20 candidates per
# call so 2048 output tokens covers titles + summaries + boundary suggestions.
_MAX_OUTPUT_TOKENS = 4096

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
                    # Boundary adjustments — the model can shift start/end
                    # within a bounded range to capture buildup or reaction
                    # context that the local candidate generator missed.
                    # Values are absolute seconds in the source video.
                    "adjusted_start_seconds": {"type": "number"},
                    "adjusted_end_seconds": {"type": "number"},
                    "boundary_reason": {"type": "string"},
                },
                "required": ["candidate_index", "title", "summary", "llm_score"],
                "propertyOrdering": [
                    "candidate_index",
                    "title",
                    "summary",
                    "llm_score",
                    "reason_tags",
                    "adjusted_start_seconds",
                    "adjusted_end_seconds",
                    "boundary_reason",
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
    # None means "leave the candidate's original boundary unchanged".
    adjusted_start_seconds: float | None = None
    adjusted_end_seconds: float | None = None
    boundary_reason: str = ""


def is_configured() -> bool:
    return bool(get_settings().gemini_api_key)


def rerank_with_gemini(
    candidates: list[Candidate],
    *,
    top_n: int,
    vibe: str,
    language: str | None,
    model: str = DEFAULT_MODEL,
    max_pre_roll: float = 15.0,
    max_clip_seconds: float = 60.0,
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

    chosen_model = model.strip() or DEFAULT_MODEL
    prompt = _build_prompt(
        candidates,
        top_n=top_n,
        vibe=vibe,
        language=language,
        max_pre_roll=max_pre_roll,
        max_clip_seconds=max_clip_seconds,
    )

    is_pro = chosen_model.startswith("gemini-2.5-pro")

    try:
        client = genai.Client(api_key=settings.gemini_api_key)
        response = client.models.generate_content(
            model=chosen_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_RESPONSE_SCHEMA,
                # Pro benefits from a tiny bit of variability for narrative
                # arc reasoning; flash gets a tighter temperature for
                # decisive ranking.
                temperature=0.55 if is_pro else 0.4,
                max_output_tokens=_MAX_OUTPUT_TOKENS,
                # Pro has hidden thinking enabled by default — leave it on,
                # this is the whole point of using Pro. Flash gets thinking
                # disabled to keep latency snappy.
                thinking_config=(
                    types.ThinkingConfig(thinking_budget=0) if not is_pro else None
                ),
            ),
        )
    except Exception as exc:
        log.warning("gemini_call_failed", model=chosen_model, error=str(exc))
        # Pro can hit quota / regional outages — try Flash as a fallback.
        if is_pro:
            log.info("gemini_fallback_to_flash")
            try:
                response = client.models.generate_content(
                    model=FLASH_MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=_RESPONSE_SCHEMA,
                        temperature=0.4,
                        max_output_tokens=_MAX_OUTPUT_TOKENS,
                        thinking_config=types.ThinkingConfig(thinking_budget=0),
                    ),
                )
            except Exception as exc2:
                log.warning("gemini_fallback_failed", error=str(exc2))
                return None
        else:
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
            c = candidates[idx]
            adj_start: float | None = None
            adj_end: float | None = None
            if "adjusted_start_seconds" in item and item["adjusted_start_seconds"] is not None:
                try:
                    adj_start = float(item["adjusted_start_seconds"])
                    # Clamp to a sane buildup window — the LLM occasionally
                    # gets greedy and tries to grab 60s of pre-roll.
                    lower_bound = max(0.0, c.start_seconds - max_pre_roll)
                    adj_start = max(lower_bound, min(adj_start, c.start_seconds))
                except (TypeError, ValueError):
                    adj_start = None
            if "adjusted_end_seconds" in item and item["adjusted_end_seconds"] is not None:
                try:
                    adj_end = float(item["adjusted_end_seconds"])
                    # End can only move outward by max_pre_roll too (used as a
                    # generic stretch budget), and the total clip can't exceed
                    # max_clip_seconds.
                    upper_bound = c.end_seconds + max_pre_roll
                    adj_end = max(c.end_seconds, min(adj_end, upper_bound))
                except (TypeError, ValueError):
                    adj_end = None
            if adj_start is not None and adj_end is not None:
                # Final guard on total duration.
                if adj_end - adj_start > max_clip_seconds:
                    # Prefer keeping the climax (original end) — trim pre-roll.
                    adj_start = max(adj_start, adj_end - max_clip_seconds)

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
                    adjusted_start_seconds=adj_start,
                    adjusted_end_seconds=adj_end,
                    boundary_reason=str(item.get("boundary_reason") or "").strip()[:200],
                )
            )
        except (KeyError, ValueError, TypeError):
            continue

    log.info(
        "gemini_rerank_done",
        model=chosen_model,
        returned=len(picks),
        requested=top_n,
        adjusted_boundaries=sum(
            1 for p in picks if p.adjusted_start_seconds is not None
        ),
    )
    return picks[:top_n]


def _build_prompt(
    candidates: list[Candidate],
    *,
    top_n: int,
    vibe: str,
    language: str | None,
    max_pre_roll: float,
    max_clip_seconds: float,
) -> str:
    """Build the prompt sent to Gemini.

    The big addition vs. v1 is the **narrative arc / buildup rule**: gaming
    and reaction content lands when viewers see the setup that leads into
    the climax. We let the model propose `adjusted_start_seconds` /
    `adjusted_end_seconds` so it can sweep backward to include 5–15s of
    buildup (or forward to catch the reaction) without us re-engineering
    the candidate generator.
    """
    lines: list[str] = []
    lines.append(
        f"You are picking the {top_n} most clip-worthy moments from a long-form "
        f"video for SHORT-FORM social distribution (YouTube Shorts, Reels, "
        f"TikTok). Each candidate is a slice of the transcript that already "
        f"scored high on audio energy / chat reaction / keywords."
    )
    if vibe.strip():
        lines.append("")
        lines.append("CREATOR BRIEF (highest priority)")
        lines.append(
            f'The uploader wants clips that match this brief: "{vibe.strip()}".'
        )
        lines.append(
            "Weight this heavily when ranking. Prefer moments that clearly "
            "fit the brief — funny reactions, named creators, specific game "
            "rounds, hype peaks — even if their local audio score is slightly "
            "lower than other candidates. Reject candidates that don't match."
        )
    if language:
        lines.append(f"Source language: {language}.")

    lines.append("")
    lines.append("WHAT MAKES A SHORT LAND")
    lines.append(
        "1. Buildup — the setup before the climax. Clutches, comebacks, "
        "speedrun PBs, punchlines all NEED 5–15s of context first. Without "
        "that buildup the moment feels abrupt and the viewer scrolls."
    )
    lines.append(
        "2. Climax — the actual hype/funny/insightful moment. This is the "
        "candidate's peak."
    )
    lines.append(
        "3. Reaction — 1–3s of the streamer's / room's reaction after the "
        "climax pays off the tension. Cutting before this feels unfinished."
    )

    lines.append("")
    lines.append("BOUNDARY ADJUSTMENT (CRITICAL)")
    lines.append(
        f"For each pick you MAY shift its start earlier by up to "
        f"{max_pre_roll:.0f}s to include buildup, and its end later by up to "
        f"{max_pre_roll:.0f}s to include reaction. Total clip duration must "
        f"stay under {max_clip_seconds:.0f}s. Set "
        f"`adjusted_start_seconds` and `adjusted_end_seconds` to the absolute "
        f"video seconds you want. If the candidate's existing boundaries are "
        f"already correct, OMIT both fields (don't echo them). When you "
        f"adjust, set `boundary_reason` to a 1-line explanation like "
        f'"included 8s of buildup so the clutch reads", "added 2s reaction".'
    )
    lines.append(
        "RULES for adjustments: never cut mid-sentence; prefer landing the "
        "start on a natural beginning (a new sentence, a question, a 'so', "
        "'okay', 'watch', etc.). Don't pad with empty silence — buildup "
        "should have ENERGY, not dead air."
    )

    lines.append("")
    lines.append("RANKING")
    lines.append(
        "Pick clips that work standalone: a hook in the first 1–2s, a "
        "satisfying payoff, and a self-contained narrative. Avoid two picks "
        "that cover the same beat."
    )
    lines.append(
        "For each pick, write a punchy <= 80-character title (no clickbait "
        "that misrepresents), a 1–2 sentence summary of WHY the moment "
        'lands, and 1–5 short reason tags (e.g. "clutch", "buildup payoff", '
        '"audience reaction", "punchline", "comeback").'
    )
    lines.append(
        "Score each pick 0..1 where 1.0 = \"definitely use this clip\" and "
        "0.3 = \"weak, skip if you have better\". Be picky — return fewer "
        "than the cap if the rest are weak."
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
        f"ordered best-first. Remember to include adjusted_start_seconds / "
        f"adjusted_end_seconds whenever the candidate is missing buildup or "
        f"reaction context."
    )
    return "\n".join(lines)
