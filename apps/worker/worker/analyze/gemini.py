"""Gemini 3.x (Pro / Flash) rerank for highlight candidates.

Model IDs are resolved from settings so we can track the newest Gemini
releases without code changes. Defaults: gemini-3.5-flash (stable) and
gemini-3.1-pro-preview. See https://ai.google.dev/gemini-api/docs/models
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import structlog

from ..config import get_settings
from .candidates import Candidate
from .chat_features import ChatDensitySeries, ChatEventOut
from .signal_timeline import format_candidate_timeline

log = structlog.get_logger(__name__)

# Logical tiers — actual model IDs come from settings (newest Gemini 3.x).
TIER_PRO = "pro"
TIER_FLASH = "flash"
DEFAULT_MODEL = TIER_FLASH

# Legacy/explicit strings mapped onto current tiers for back-compat.
_LEGACY_TIER_MAP = {
    "gemini-2.5-pro": TIER_PRO,
    "gemini-2.5-flash": TIER_FLASH,
    "pro": TIER_PRO,
    "flash": TIER_FLASH,
}

_MAX_OUTPUT_TOKENS = 4096


def resolve_model(name: str | None) -> str:
    """Map a tier/legacy/explicit name to a concrete Gemini model ID."""
    settings = get_settings()
    key = (name or "").strip()
    tier = _LEGACY_TIER_MAP.get(key.lower())
    if tier == TIER_PRO:
        return settings.gemini_pro_model
    if tier == TIER_FLASH:
        return settings.gemini_flash_model
    # An explicit model ID (e.g. "gemini-3.1-flash-lite") — pass through.
    return key or settings.gemini_flash_model


def _is_gemini_3(model: str) -> bool:
    return model.lower().startswith("gemini-3")


def _build_config(model: str, *, schema: dict[str, Any] | None, max_tokens: int):
    """GenerateContentConfig tuned per model family.

    Gemini 3.x: use thinking_level, drop temperature (optimized for defaults).
    Gemini 2.5: temperature + thinking_budget=0 on flash.
    """
    from google.genai import types

    kwargs: dict[str, Any] = {"max_output_tokens": max_tokens}
    if schema is not None:
        kwargs["response_mime_type"] = "application/json"
        kwargs["response_schema"] = schema

    if _is_gemini_3(model):
        level = (get_settings().gemini_thinking_level or "low").strip().lower()
        if level not in ("minimal", "low", "medium", "high"):
            level = "low"
        # gemini-3.1-pro-preview does not support "minimal".
        if "pro" in model.lower() and level == "minimal":
            level = "low"
        kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=level)
    else:
        is_pro = "pro" in model.lower()
        kwargs["temperature"] = 0.55 if is_pro else 0.4
        if not is_pro:
            kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)

    return types.GenerateContentConfig(**kwargs)

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
                    "moment_type": {
                        "type": "string",
                        "enum": ["action", "commentary", "reaction", "setup"],
                    },
                    "confidence": {"type": "number"},
                    "reason_tags": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
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
                    "moment_type",
                    "confidence",
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

_GAMING_VIBE = re.compile(
    r"\b(cs2|counter.?strike|valorant|apex|fortnite|overwatch|league|lol|"
    r"gaming|clutch|ace|frag|fps|stream)\b",
    re.IGNORECASE,
)


@dataclass
class LlmPick:
    candidate_index: int
    title: str
    summary: str
    llm_score: float
    reason_tags: list[str]
    moment_type: str = ""
    confidence: float = 0.0
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
    chat_events: list[ChatEventOut] | None = None,
    chat_density: ChatDensitySeries | None = None,
    scene_cuts: list[float] | None = None,
    audio_samples: list[dict[str, float]] | None = None,
) -> list[LlmPick] | None:
    settings = get_settings()
    if not settings.gemini_api_key:
        log.info("gemini_skipped_no_api_key")
        return None
    if not candidates:
        return []

    try:
        from google import genai
    except ImportError as exc:
        log.warning("gemini_import_failed", error=str(exc))
        return None

    chosen_model = resolve_model(model)
    flash_model = settings.gemini_flash_model
    prompt = _build_prompt(
        candidates,
        top_n=top_n,
        vibe=vibe,
        language=language,
        max_pre_roll=max_pre_roll,
        max_clip_seconds=max_clip_seconds,
        chat_events=chat_events or [],
        chat_density=chat_density,
        scene_cuts=scene_cuts,
        audio_samples=audio_samples,
    )

    try:
        client = genai.Client(api_key=settings.gemini_api_key)
        response = client.models.generate_content(
            model=chosen_model,
            contents=prompt,
            config=_build_config(
                chosen_model, schema=_RESPONSE_SCHEMA, max_tokens=_MAX_OUTPUT_TOKENS
            ),
        )
    except Exception as exc:
        log.warning("gemini_call_failed", model=chosen_model, error=str(exc))
        if chosen_model != flash_model:
            log.info("gemini_fallback_to_flash", fallback=flash_model)
            try:
                response = client.models.generate_content(
                    model=flash_model,
                    contents=prompt,
                    config=_build_config(
                        flash_model,
                        schema=_RESPONSE_SCHEMA,
                        max_tokens=_MAX_OUTPUT_TOKENS,
                    ),
                )
                chosen_model = flash_model
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
                    lower_bound = max(0.0, c.start_seconds - max_pre_roll)
                    adj_start = max(lower_bound, min(adj_start, c.start_seconds))
                except (TypeError, ValueError):
                    adj_start = None
            if "adjusted_end_seconds" in item and item["adjusted_end_seconds"] is not None:
                try:
                    adj_end = float(item["adjusted_end_seconds"])
                    upper_bound = c.end_seconds + max_pre_roll
                    adj_end = max(c.end_seconds, min(adj_end, upper_bound))
                except (TypeError, ValueError):
                    adj_end = None
            if adj_start is not None and adj_end is not None:
                if adj_end - adj_start > max_clip_seconds:
                    adj_start = max(adj_start, adj_end - max_clip_seconds)

            moment_type = str(item.get("moment_type") or "").strip()[:32]
            confidence = max(0.0, min(1.0, float(item.get("confidence", 0.5))))

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
                    moment_type=moment_type,
                    confidence=confidence,
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
    chat_events: list[ChatEventOut],
    chat_density: ChatDensitySeries | None,
    scene_cuts: list[float] | None,
    audio_samples: list[dict[str, float]] | None,
) -> str:
    lines: list[str] = []
    lines.append(
        f"You are picking the {top_n} most clip-worthy moments from a long-form "
        f"video for SHORT-FORM social distribution (YouTube Shorts, Reels, TikTok). "
        f"Each candidate was scored on audio energy, chat reaction, and keywords. "
        f"Some candidates are anchored on AUDIO/CHAT PEAKS (the actual hype moment), "
        f"not just speech."
    )

    if vibe.strip():
        lines.append("")
        lines.append("CREATOR BRIEF (highest priority)")
        lines.append(f'The uploader wants clips matching: "{vibe.strip()}".')
        lines.append(
            "Weight this heavily. Reject candidates that don't match even if "
            "their local score is high."
        )

    if _GAMING_VIBE.search(vibe):
        lines.append("")
        lines.append("GAMING CONTEXT")
        lines.append(
            "Clutch = kill audio spike + chat burst + streamer reaction. "
            "Callout lines ('B site', 'rotate', 'he's low') are SETUP, not climax. "
            "BAD: commentary describing a play after it happened. "
            "GOOD: buildup → gunfire/kill spike → reaction."
        )

    if language:
        lines.append(f"Source language: {language}.")

    lines.append("")
    lines.append("TWELVELABS VISUAL EVIDENCE")
    lines.append(
        "Some candidates include TwelveLabs video-native evidence (segment_type, "
        "visual_reason, confidence). Prioritize actual visible action/reaction over "
        "transcript-only commentary. If visual evidence says action happens earlier "
        "than the transcript peak, prefer the earlier visual moment and adjust boundaries."
    )
    lines.append(
        "If chat/audio spikes happen after a visual event, include enough pre-roll "
        "to show what caused the reaction."
    )

    lines.append("")
    lines.append("COMMENTARY VS ACTION (CRITICAL)")
    lines.append(
        "Do NOT pick moments where the streamer is DESCRIBING a play after it "
        "happened unless the audio/chat peak is inside the window. When "
        "peak_offset is negative, the hype happened BEFORE the speech — shift "
        "boundaries toward audio_peak_at / chat_peak_at, not the sentence about it."
    )
    lines.append(
        "Set moment_type: action (climax/play), commentary (describing after), "
        "reaction (post-climax), setup (pre-play). Prefer action + reaction. "
        "Skip pure commentary unless nothing better exists."
    )

    lines.append("")
    lines.append("WHAT MAKES A SHORT LAND")
    lines.append("1. Buildup — 5–15s context before climax.")
    lines.append("2. Climax — the actual hype/funny moment (audio/chat peak).")
    lines.append("3. Reaction — 1–3s after climax.")

    lines.append("")
    lines.append("BOUNDARY ADJUSTMENT (CRITICAL)")
    lines.append(
        f"Shift start earlier up to {max_pre_roll:.0f}s for buildup, end later "
        f"up to {max_pre_roll:.0f}s for reaction. Max duration {max_clip_seconds:.0f}s. "
        f"Set adjusted_start_seconds / adjusted_end_seconds as absolute video seconds."
    )

    lines.append("")
    lines.append("RANKING")
    lines.append(
        "Standalone clips with hook in first 1–2s. Score 0..1. Return fewer "
        "than cap if weak. Include confidence 0..1."
    )

    # Lazy audio series wrapper for timeline formatting
    from .audio_features import AudioFeatureSeries

    audio_series = (
        AudioFeatureSeries(samples=audio_samples, duration_seconds=len(audio_samples))
        if audio_samples
        else None
    )

    lines.append("")
    lines.append("CANDIDATES:")
    for i, c in enumerate(candidates):
        body = c.text[:600] + ("…" if len(c.text) > 600 else "")
        peak_bits: list[str] = []
        if c.audio_peak_at is not None:
            peak_bits.append(f"audio_peak_at={c.audio_peak_at:.1f}s")
        if c.chat_peak_at is not None:
            peak_bits.append(f"chat_peak_at={c.chat_peak_at:.1f}s")
        offset = c.peak_offset_from_start
        if offset is not None:
            peak_bits.append(f"peak_offset_from_start={offset:+.1f}s")
        peak_bits.append(f"seed={c.seed_source}")

        lines.append(
            f"[{i}] {c.start_seconds:.1f}s–{c.end_seconds:.1f}s "
            f"(dur {c.duration_seconds:.1f}s, local_score {c.composite_score:.2f}, "
            f"audio {c.audio_score:.2f}, chat {c.chat_score:.2f}, kw {c.keyword_score:.2f}, "
            f"{', '.join(peak_bits)})\n"
            f"    \"{body}\""
        )

        timeline = format_candidate_timeline(
            c.start_seconds,
            c.end_seconds,
            audio=audio_series,
            chat=chat_density,
            scene_cuts=scene_cuts,
        )
        if timeline:
            lines.append(f"    SIGNALS:\n    {timeline.replace(chr(10), chr(10) + '    ')}")

        if chat_density and chat_events:
            top_chat = chat_density.top_messages_in_window(
                chat_events, c.start_seconds, c.end_seconds, limit=5
            )
            if top_chat:
                chat_lines = [
                    f"@{ev.username or 'anon'}: {(ev.message or '')[:80]}"
                    for ev in top_chat
                ]
                lines.append(f"    CHAT: {' | '.join(chat_lines)}")

        visual_score = getattr(c, "visual_score", 0.0)
        fusion_score = getattr(c, "fusion_score", 0.0)
        visual_ev = getattr(c, "visual_evidence", {}) or {}
        reason_json = getattr(c, "reason_json", {}) or {}
        if visual_score > 0 or visual_ev:
            tl_bits = [
                f"visual_score={visual_score:.2f}",
                f"fusion_score={fusion_score:.2f}",
            ]
            if visual_ev.get("segment_type"):
                tl_bits.append(f"twelvelabs_segment_type={visual_ev['segment_type']}")
            if visual_ev.get("confidence"):
                tl_bits.append(f"twelvelabs_confidence={visual_ev['confidence']:.2f}")
            for key in ("visual_reason", "audio_reason", "speech_reason", "description"):
                if visual_ev.get(key):
                    tl_bits.append(f"twelvelabs_{key}={str(visual_ev[key])[:120]}")
            agreement = (reason_json.get("scores") or {}).get("provider_agreement")
            if agreement is not None:
                tl_bits.append(f"provider_agreement={agreement:.2f}")
            lines.append(f"    TWELVELABS: {', '.join(tl_bits)}")

    lines.append("")
    lines.append(
        f"Return JSON with at most {top_n} highlights, ordered best-first."
    )
    return "\n".join(lines)
