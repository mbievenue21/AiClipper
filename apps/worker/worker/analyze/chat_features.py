"""Chat density signal for VOD highlights.

yt-dlp's Twitch chat dump is a JSON list where every comment has
``content_offset_seconds`` (offset in the VOD) and a ``message`` object.
We persist parsed messages to ``chat_events`` (so the UI can show them
later) and return a per-second density series for highlight scoring.

YouTube live chat dumps use a different shape; for now we only parse
Twitch and silently skip everything else.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)


@dataclass
class ChatEventOut:
    timestamp_seconds: float
    username: str | None
    message: str | None
    emote_count: int
    message_type: str


@dataclass
class ChatDensitySeries:
    """Per-second message counts, normalised to 0..1 across the clip."""

    raw_per_second: list[float]
    normalised: list[float]
    total_messages: int

    def density_window(self, start: float, end: float) -> float:
        if not self.normalised or end <= start:
            return 0.0
        i = max(0, int(round(start)))
        j = max(i + 1, min(len(self.normalised), int(round(end)) + 1))
        slice_ = self.normalised[i:j]
        return float(sum(slice_) / len(slice_)) if slice_ else 0.0


def parse_twitch_chat(chat_json_path: Path) -> list[ChatEventOut]:
    """Yield events from a yt-dlp Twitch chat dump. Best-effort, swallows malformed rows."""
    try:
        raw = json.loads(chat_json_path.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("chat_parse_failed", path=str(chat_json_path), error=str(exc))
        return []

    if not isinstance(raw, list):
        # Some dumps wrap the comments in {"comments": [...]}.
        if isinstance(raw, dict) and isinstance(raw.get("comments"), list):
            raw = raw["comments"]
        else:
            log.warning("chat_unexpected_shape", path=str(chat_json_path))
            return []

    out: list[ChatEventOut] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            ts = entry.get("content_offset_seconds")
            if ts is None:
                ts = entry.get("offset")
            if ts is None:
                continue
            ts = float(ts)

            commenter = entry.get("commenter") or {}
            username = (
                commenter.get("display_name") or commenter.get("name") if isinstance(commenter, dict) else None
            )

            msg = entry.get("message")
            text = None
            emote_count = 0
            mtype = "chat"
            if isinstance(msg, dict):
                text = msg.get("body") or msg.get("text")
                emotes = msg.get("emoticons") or []
                if isinstance(emotes, list):
                    emote_count = len(emotes)
                if msg.get("user_notice_params"):
                    mtype = "notice"
            elif isinstance(msg, str):
                text = msg

            out.append(
                ChatEventOut(
                    timestamp_seconds=ts,
                    username=username,
                    message=text,
                    emote_count=emote_count,
                    message_type=mtype,
                )
            )
        except (TypeError, ValueError):
            continue

    log.info("chat_parsed", events=len(out), path=str(chat_json_path))
    return out


def compute_chat_density(
    events: list[ChatEventOut], *, duration_seconds: float
) -> ChatDensitySeries:
    """Bin chat events per second, then normalise against the 95th percentile."""
    n_seconds = max(1, int(duration_seconds) + 1)
    counts = [0.0] * n_seconds
    for ev in events:
        idx = max(0, min(n_seconds - 1, int(ev.timestamp_seconds)))
        # Subs / cheers / raids carry extra weight — they're stronger reaction signals.
        weight = 1.0
        if ev.message_type and ev.message_type != "chat":
            weight = 3.0
        elif ev.emote_count >= 3:
            weight = 1.5
        counts[idx] += weight

    if not events:
        return ChatDensitySeries(raw_per_second=counts, normalised=[0.0] * n_seconds, total_messages=0)

    # Normalise against p95 to avoid one massive spike flattening everything else.
    sorted_counts = sorted(counts)
    p95_idx = max(0, int(len(sorted_counts) * 0.95) - 1)
    p95 = max(1e-6, sorted_counts[p95_idx])
    normalised = [min(1.0, c / p95) for c in counts]

    return ChatDensitySeries(
        raw_per_second=counts,
        normalised=normalised,
        total_messages=len(events),
    )


def iter_existing_events(rows: Iterator[Any]) -> list[ChatEventOut]:
    """Convert SQLAlchemy ChatEvent rows into our internal ChatEventOut shape."""
    out: list[ChatEventOut] = []
    for r in rows:
        out.append(
            ChatEventOut(
                timestamp_seconds=float(r.timestamp_seconds or 0.0),
                username=r.username,
                message=r.message,
                emote_count=int(r.emote_count or 0),
                message_type=r.message_type or "chat",
            )
        )
    return out
