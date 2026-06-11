"""Detect video source platform from a URL (mirrors apps/web/lib/source-url.ts)."""

from __future__ import annotations

from urllib.parse import urlparse


def detect_source_type(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Enter a valid http(s) URL.")

    host = parsed.netloc.removeprefix("www.").lower()

    if (
        host == "youtube.com"
        or host == "youtu.be"
        or host == "m.youtube.com"
        or host.endswith(".youtube.com")
    ):
        return "youtube"

    if (
        host == "twitch.tv"
        or host.endswith(".twitch.tv")
        or host == "clips.twitch.tv"
    ):
        return "twitch"

    raise ValueError(
        "Unsupported URL. Use a YouTube or Twitch link for reference clip download."
    )
