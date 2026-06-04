"""Publish stage (Step 12 + 13): upload a clip to YouTube / Instagram.

Both backends live behind a tiny common protocol so the job handler can
treat them uniformly. Backends raise ``PublishError`` for any predictable
failure (auth expired, video too short, etc.) and return an
``UploadResult`` on success.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .errors import AuthExpiredError, PublishError


@dataclass(frozen=True)
class UploadResult:
    external_id: str
    external_url: str | None
    raw: dict | None = None


class Uploader(Protocol):
    """Minimal common surface implemented by each platform backend."""

    async def upload(
        self,
        *,
        access_token: str,
        refresh_token: str | None,
        video_path: str,
        title: str,
        description: str | None,
        tags: list[str],
        visibility: str,
    ) -> UploadResult: ...


__all__ = [
    "AuthExpiredError",
    "PublishError",
    "UploadResult",
    "Uploader",
    "get_uploader",
]


def get_uploader(platform: str) -> Uploader:
    """Resolve the uploader for the given platform."""
    if platform == "youtube":
        from .youtube import YouTubeUploader

        return YouTubeUploader()
    if platform == "instagram":
        from .instagram import InstagramUploader

        return InstagramUploader()
    raise PublishError(f"Unknown publish platform: {platform!r}")
