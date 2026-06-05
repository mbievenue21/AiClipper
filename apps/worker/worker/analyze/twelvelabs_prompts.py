"""Prompt and query builders for TwelveLabs Multimodal Analysis."""

from __future__ import annotations

import json
import re

from ..providers.twelvelabs_types import TwelveLabsPromptContext

_GAMING_VIBE = re.compile(
    r"\b(cs2|counter.?strike|valorant|apex|fortnite|overwatch|league|lol|"
    r"gaming|clutch|ace|frag|fps|stream)\b",
    re.IGNORECASE,
)
_FUNNY_VIBE = re.compile(
    r"\b(funny|comedy|hilarious|laugh|meme|joke)\b",
    re.IGNORECASE,
)
_EDU_VIBE = re.compile(
    r"\b(educational|tutorial|explain|guide|how to|tips)\b",
    re.IGNORECASE,
)

PEGASUS_SEGMENT_TYPES = [
    "streamer_reaction",
    "gameplay_win",
    "gameplay_fail",
    "rage_moment",
    "funny_visual",
    "surprised_reaction",
    "jump_scare",
    "chat_hype_visual",
    "on_screen_event",
    "visual_payoff",
    "high_energy_action",
    "awkward_or_unexpected_moment",
    "commentary_only",
    "dead_air_or_menu",
]


def build_twelvelabs_search_queries(vibe: str) -> list[str]:
    """Marengo semantic search queries for streamer highlights."""
    queries = [
        "streamer has a big surprised reaction",
        "streamer laughs or reacts to something funny",
        "streamer gets angry or frustrated",
        "unexpected gameplay fail",
        "impressive gameplay win or clutch moment",
        "chat reacts strongly to something happening on screen",
        "visually funny moment",
        "jump scare or sudden panic reaction",
        "high energy action moment",
        "streamer says clip that or chat says clip it",
        "awkward unexpected moment",
        "on screen event causes visible reaction",
        "moment with clear setup and payoff",
    ]
    if _GAMING_VIBE.search(vibe):
        queries.extend(
            [
                "FPS clutch kill or multi kill",
                "player wins a fight after intense action",
                "player dies unexpectedly and reacts",
                "funny missed shot or failed play",
                "team fight with visible payoff",
                "panic during combat",
            ]
        )
    if _FUNNY_VIBE.search(vibe):
        queries.extend(
            [
                "funniest streamer reaction",
                "chat laughing at streamer",
                "visual joke or awkward moment",
                "streamer realizes mistake and reacts",
            ]
        )
    if _EDU_VIBE.search(vibe):
        queries.extend(
            [
                "clear useful explanation with strong visual context",
                "important explanation supported by on screen event",
            ]
        )
    return queries


def build_twelvelabs_segmentation_prompt(ctx: TwelveLabsPromptContext) -> str:
    """Pegasus structured segmentation prompt."""
    lines = [
        "You are analyzing a long-form streamer VOD to find short-form highlight clips.",
        "Identify timestamped segments that are clip-worthy for YouTube Shorts, Reels, or TikTok.",
        "",
        "RULES:",
        "- Prefer moments with visible action, visible streamer reaction, on-screen events, or clear payoff.",
        "- Do NOT select long explanation/commentary unless it has strong visual or emotional payoff.",
        "- If chat reacts after the actual moment, move suggested_clip_start earlier to include the cause.",
        "- If transcript is exciting but video is visually static, lower confidence.",
        "- Silent visual streamer reactions can still be high-value highlights.",
        "- Flag commentary_only and dead_air_or_menu segments with low clip-worthiness.",
        "",
        "For gaming/FPS: prioritize clutch kills, surprising deaths, wins, fails, jump scares, "
        "panic reactions, chat-hype moments, visible frustration, funny missed shots.",
        "",
        f"Segment types to use: {', '.join(PEGASUS_SEGMENT_TYPES)}",
    ]
    if ctx.vibe.strip():
        lines.extend(["", f"Creator vibe/brief: {ctx.vibe.strip()}"])
    if ctx.language:
        lines.append(f"Language: {ctx.language}")
    if ctx.transcript_summary:
        lines.append(f"Transcript summary: {ctx.transcript_summary[:1200]}")
    if ctx.audio_peak_times:
        peaks = ", ".join(f"{t:.1f}s" for t in ctx.audio_peak_times[:12])
        lines.append(f"Local audio excitement peaks: {peaks}")
    if ctx.chat_peak_times:
        peaks = ", ".join(f"{t:.1f}s" for t in ctx.chat_peak_times[:12])
        lines.append(f"Local chat density peaks: {peaks}")
    if ctx.scene_cuts:
        cuts = ", ".join(f"{t:.1f}s" for t in ctx.scene_cuts[:20])
        lines.append(f"Scene cuts: {cuts}")

    lines.extend(
        [
            "",
            "Return strict JSON with a 'segments' array. Each segment must include:",
            json.dumps(
                {
                    "start_seconds": 0,
                    "end_seconds": 0,
                    "segment_type": "streamer_reaction",
                    "confidence": 0.0,
                    "title": "",
                    "description": "",
                    "visual_reason": "",
                    "audio_reason": "",
                    "speech_reason": "",
                    "why_clip_worthy": "",
                    "is_commentary_only": False,
                    "is_dead_air_or_menu": False,
                    "suggested_clip_start_seconds": 0,
                    "suggested_clip_end_seconds": 0,
                },
                indent=2,
            ),
        ]
    )
    return "\n".join(lines)


def pegasus_response_schema() -> dict:
    """JSON schema for Pegasus segmentation output."""
    return {
        "type": "object",
        "properties": {
            "segments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "start_seconds": {"type": "number"},
                        "end_seconds": {"type": "number"},
                        "segment_type": {"type": "string"},
                        "confidence": {"type": "number"},
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "visual_reason": {"type": "string"},
                        "audio_reason": {"type": "string"},
                        "speech_reason": {"type": "string"},
                        "why_clip_worthy": {"type": "string"},
                        "is_commentary_only": {"type": "boolean"},
                        "is_dead_air_or_menu": {"type": "boolean"},
                        "suggested_clip_start_seconds": {"type": "number"},
                        "suggested_clip_end_seconds": {"type": "number"},
                    },
                    "required": ["start_seconds", "end_seconds", "segment_type", "confidence"],
                },
            }
        },
        "required": ["segments"],
    }
