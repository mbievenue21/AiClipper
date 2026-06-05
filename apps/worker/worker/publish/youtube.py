"""YouTube Data API v3 upload.

We rely on the user having already completed the OAuth flow in the Next.js
app (see apps/web/app/api/auth/youtube/...). The ``access_token`` and
``refresh_token`` are read from the ``accounts`` table.

Token refresh is done lazily here: if the access token is expired we use
the refresh token + the env-provided client id/secret to mint a new one,
then proceed with the upload.

For safety, every clip is uploaded as PRIVATE by default — the user has to
explicitly flip visibility to public/unlisted in the schedule dialog.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

import httpx
import structlog

from . import UploadResult, Uploader
from .errors import AuthExpiredError, PublishError

log = structlog.get_logger(__name__)

UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=multipart&part=snippet,status"
REFRESH_URL = "https://oauth2.googleapis.com/token"

_YT_MAX_TAG_LEN = 30
_YT_MAX_TAGS_TOTAL = 500
_YT_MAX_TAG_COUNT = 30


def _sanitize_youtube_tags(tags: list[str]) -> list[str]:
    """YouTube rejects uploads when any tag exceeds 30 chars or total > 500."""
    seen: set[str] = set()
    out: list[str] = []
    total = 0
    for raw in tags:
        t = str(raw).strip().lower()
        t = re.sub(r"^#+", "", t)
        t = re.sub(r"[^a-z0-9 _-]", "", t).strip()
        t = re.sub(r"\s+", " ", t)
        if not t:
            continue
        if len(t) > _YT_MAX_TAG_LEN:
            trimmed = t[:_YT_MAX_TAG_LEN]
            cut = trimmed.rfind(" ")
            t = (trimmed[:cut] if cut > 8 else trimmed).strip()
            if not t:
                continue
        if t in seen:
            continue
        if total + len(t) > _YT_MAX_TAGS_TOTAL:
            break
        seen.add(t)
        out.append(t)
        total += len(t)
        if len(out) >= _YT_MAX_TAG_COUNT:
            break
    return out


def _format_youtube_error(resp: httpx.Response) -> str:
    try:
        data = resp.json()
        err = data.get("error", {})
        parts: list[str] = []
        if err.get("message"):
            parts.append(str(err["message"]))
        for item in err.get("errors") or []:
            reason = item.get("reason") or "error"
            msg = item.get("message") or ""
            parts.append(f"{reason}: {msg}".strip(": "))
        if parts:
            return "; ".join(parts)
    except Exception:
        pass
    return resp.text[:500]


def _category_for_clip() -> str:
    # 24 = Entertainment (good default for short-form). The user can change
    # the category later from YouTube Studio.
    return "24"


async def _refresh_access_token(refresh_token: str) -> str:
    client_id = os.environ.get("YOUTUBE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise AuthExpiredError(
            "Cannot refresh YouTube token — set YOUTUBE_CLIENT_ID and "
            "YOUTUBE_CLIENT_SECRET in .env."
        )
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            REFRESH_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
    if resp.status_code != 200:
        raise AuthExpiredError(
            f"Refresh failed ({resp.status_code}): {resp.text[:200]}"
        )
    return resp.json()["access_token"]


class YouTubeUploader(Uploader):
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
    ) -> UploadResult:
        path = Path(video_path)
        if not path.exists():
            raise PublishError(f"Video file missing on disk: {path}")

        safe_tags = _sanitize_youtube_tags(tags)
        metadata = {
            "snippet": {
                "title": title[:100],  # YT hard limit
                "description": (description or "")[:5000],
                "tags": safe_tags,
                "categoryId": _category_for_clip(),
            },
            "status": {
                "privacyStatus": visibility,  # private | unlisted | public
                "selfDeclaredMadeForKids": False,
                "embeddable": True,
            },
        }

        body = await asyncio.to_thread(path.read_bytes)

        files = {
            "metadata": (None, json.dumps(metadata), "application/json"),
            "video": (path.name, body, "video/*"),
        }

        async def _do(token: str) -> httpx.Response:
            async with httpx.AsyncClient(timeout=900) as client:
                return await client.post(
                    UPLOAD_URL,
                    headers={"Authorization": f"Bearer {token}"},
                    files=files,
                )

        resp = await _do(access_token)
        if resp.status_code == 401 and refresh_token:
            log.info("youtube_token_refresh")
            new_token = await _refresh_access_token(refresh_token)
            resp = await _do(new_token)

        if resp.status_code >= 400:
            detail = _format_youtube_error(resp)
            raise PublishError(
                f"YouTube upload failed ({resp.status_code}): {detail}"
            )

        data = resp.json()
        video_id = data.get("id")
        if not video_id:
            raise PublishError(f"YouTube returned no video id: {data}")

        return UploadResult(
            external_id=video_id,
            external_url=f"https://youtu.be/{video_id}",
            raw=data,
        )
