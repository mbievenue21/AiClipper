"""Instagram Reels publishing via the Instagram Graph API (Business Login).

Flow
----
Instagram doesn't accept a multipart binary upload — it ingests Reels by
URL. The video file must live at a public HTTPS URL that Instagram's
fetcher can hit. We construct that URL from ``NEXT_PUBLIC_APP_URL`` +
``/api/media/<relative_path>``. In local development this means the user
has to expose ``localhost`` via a tunnel (Cloudflared / ngrok) — same
setup as the OAuth callback.

Three API calls in order:

    1.  POST {ig-user-id}/media?media_type=REELS&video_url=...&caption=...
        → returns ``{ id: container_id }``
    2.  GET  {container_id}?fields=status_code   (poll)
        → wait until ``status_code == "FINISHED"`` (1-3 min typical)
    3.  POST {ig-user-id}/media_publish?creation_id={container_id}
        → returns ``{ id: media_id }`` — the actual IG post

Token model
-----------
Instagram Business Login returns a 60-day "long-lived" token (no refresh
token in the OAuth2 sense). To extend it we GET
``/refresh_access_token`` with that token; the response is a fresh
60-day token. We attempt that refresh exactly once when the publish call
returns 401/190.

Notes
-----
- Instagram doesn't have a "private / unlisted / public" toggle — every
  published Reel is public to the account's followers. The schedule
  dialog's ``visibility`` field is therefore ignored here (but kept in the
  signature so the dispatcher is uniform across platforms).
- Tags are appended to the caption as ``#tag`` hashtags. IG's caption
  cap is 2200 characters; we trim conservatively.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from urllib.parse import quote

import httpx
import structlog

from ..config import get_settings
from . import UploadResult, Uploader
from .errors import AuthExpiredError, PublishError

log = structlog.get_logger(__name__)

GRAPH_BASE = "https://graph.instagram.com/v23.0"
REFRESH_URL = "https://graph.instagram.com/refresh_access_token"

# Max time we'll wait for IG to finish ingesting the video before giving up.
INGEST_TIMEOUT_SECONDS = 8 * 60
# How often we poll the container while it's IN_PROGRESS.
INGEST_POLL_INTERVAL_SECONDS = 5
# IG caption hard limit is 2200, we leave some safety margin.
MAX_CAPTION_CHARS = 2100


def _public_media_url(video_path: str) -> str:
    """Build the URL Instagram's fetcher will hit.

    The worker only knows the absolute disk path. We translate it back into
    the project-relative form the web app's ``/api/media/[...]`` route
    serves.
    """
    media_root = get_settings().media_root_path.resolve()
    abs_path = Path(video_path).resolve()
    try:
        rel = abs_path.relative_to(media_root)
    except ValueError as exc:
        raise PublishError(
            f"Clip file {abs_path} is not under MEDIA_ROOT {media_root}; "
            "Instagram needs a public URL we can serve."
        ) from exc

    base = (os.environ.get("NEXT_PUBLIC_APP_URL") or "").strip()
    if not base:
        raise PublishError(
            "NEXT_PUBLIC_APP_URL is not set. Instagram needs a public HTTPS "
            "URL to fetch the video — point this at your Cloudflare tunnel "
            "or hosted domain."
        )
    if not base.startswith(("http://", "https://")):
        raise PublishError(
            f"NEXT_PUBLIC_APP_URL must start with http(s):// (got {base!r})."
        )
    if base.startswith("http://"):
        # Instagram silently fails / 400s on http URLs in some regions.
        log.warning("instagram_insecure_app_url", url=base)

    rel_str = "/".join(quote(part, safe="") for part in rel.parts)
    return f"{base.rstrip('/')}/api/media/{rel_str}"


def _build_caption(title: str, description: str | None, tags: list[str]) -> str:
    pieces: list[str] = [title.strip()]
    if description:
        pieces.append(description.strip())
    cleaned_tags = []
    for t in tags or []:
        t = t.strip().lstrip("#")
        if not t:
            continue
        # IG hashtags must be alnum + underscores. Drop anything else.
        t = re.sub(r"[^A-Za-z0-9_]", "", t)
        if t:
            cleaned_tags.append(f"#{t}")
    if cleaned_tags:
        pieces.append(" ".join(cleaned_tags[:30]))  # IG caps at 30 hashtags
    caption = "\n\n".join(p for p in pieces if p)
    return caption[:MAX_CAPTION_CHARS]


async def _refresh_long_lived(access_token: str) -> str:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            REFRESH_URL,
            params={
                "grant_type": "ig_refresh_token",
                "access_token": access_token,
            },
        )
    if resp.status_code != 200:
        raise AuthExpiredError(
            f"Instagram refresh failed ({resp.status_code}): {resp.text[:300]}"
        )
    return resp.json().get("access_token", access_token)


def _is_auth_error(resp: httpx.Response) -> bool:
    if resp.status_code in (401, 403):
        return True
    # IG error codes 190 (expired token) and 102 (session invalid).
    try:
        err = resp.json().get("error") or {}
        code = err.get("code")
        if code in (190, 102, 463, 467):
            return True
    except Exception:
        pass
    return False


def _raise_for_error(resp: httpx.Response, context: str) -> None:
    if resp.status_code < 400:
        return
    try:
        body = resp.json()
        err = body.get("error") or {}
        msg = (
            err.get("error_user_msg")
            or err.get("message")
            or err.get("type")
            or resp.text[:300]
        )
    except Exception:
        msg = resp.text[:300]
    if _is_auth_error(resp):
        raise AuthExpiredError(f"Instagram {context}: {msg}")
    raise PublishError(f"Instagram {context} ({resp.status_code}): {msg}")


class InstagramUploader(Uploader):
    async def upload(
        self,
        *,
        access_token: str,
        refresh_token: str | None,  # unused — IG doesn't issue refresh tokens
        video_path: str,
        title: str,
        description: str | None,
        tags: list[str],
        visibility: str,  # unused on IG
    ) -> UploadResult:
        del refresh_token, visibility  # explicitly unused

        path = Path(video_path)
        if not path.exists():
            raise PublishError(f"Video file missing on disk: {path}")

        video_url = _public_media_url(video_path)
        caption = _build_caption(title, description, tags)
        log.info(
            "instagram_upload_start",
            file=str(path),
            public_url=video_url,
            caption_chars=len(caption),
        )

        token = access_token
        refreshed = False

        async def _request(method: str, url: str, **kw) -> httpx.Response:
            nonlocal token, refreshed
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.request(method, url, **kw)
            if _is_auth_error(resp) and not refreshed:
                log.info("instagram_token_refresh")
                token = await _refresh_long_lived(token)
                refreshed = True
                # Re-issue with the refreshed token. The caller is
                # responsible for injecting `access_token=...` via the
                # `params` kwarg so we just patch that here.
                if "params" in kw and isinstance(kw["params"], dict):
                    kw["params"] = {**kw["params"], "access_token": token}
                async with httpx.AsyncClient(timeout=120) as client:
                    resp = await client.request(method, url, **kw)
            return resp

        # ----- 1. Create the media container -----
        create_params = {
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "share_to_feed": "true",
            "access_token": token,
        }
        create_resp = await _request(
            "POST", f"{GRAPH_BASE}/me/media", params=create_params
        )
        _raise_for_error(create_resp, "container creation")
        container_id = create_resp.json().get("id")
        if not container_id:
            raise PublishError(
                f"Instagram returned no container id: {create_resp.text[:200]}"
            )
        log.info("instagram_container_created", container_id=container_id)

        # ----- 2. Poll until status_code is FINISHED -----
        deadline = asyncio.get_event_loop().time() + INGEST_TIMEOUT_SECONDS
        last_status: str | None = None
        while True:
            if asyncio.get_event_loop().time() > deadline:
                raise PublishError(
                    f"Instagram never finished ingesting after "
                    f"{INGEST_TIMEOUT_SECONDS}s. Last status: {last_status}"
                )
            await asyncio.sleep(INGEST_POLL_INTERVAL_SECONDS)
            status_resp = await _request(
                "GET",
                f"{GRAPH_BASE}/{container_id}",
                params={
                    "fields": "status_code,status",
                    "access_token": token,
                },
            )
            _raise_for_error(status_resp, "container status")
            body = status_resp.json()
            last_status = body.get("status_code")
            log.info(
                "instagram_container_poll",
                container_id=container_id,
                status_code=last_status,
                status=body.get("status"),
            )
            if last_status == "FINISHED":
                break
            if last_status in ("ERROR", "EXPIRED"):
                raise PublishError(
                    f"Instagram ingest failed: status={last_status} "
                    f"detail={body.get('status') or ''!r}"
                )
            # PUBLISHED, IN_PROGRESS, IN_REVIEW → keep polling

        # ----- 3. Publish the container -----
        publish_resp = await _request(
            "POST",
            f"{GRAPH_BASE}/me/media_publish",
            params={"creation_id": container_id, "access_token": token},
        )
        _raise_for_error(publish_resp, "publish")
        publish_body = publish_resp.json()
        media_id = publish_body.get("id")
        if not media_id:
            raise PublishError(
                f"Instagram publish returned no media id: {publish_resp.text[:200]}"
            )

        # Fetch permalink (best effort — failure here doesn't fail the upload).
        permalink: str | None = None
        try:
            link_resp = await _request(
                "GET",
                f"{GRAPH_BASE}/{media_id}",
                params={"fields": "permalink", "access_token": token},
            )
            if link_resp.status_code < 400:
                permalink = link_resp.json().get("permalink")
        except Exception as exc:
            log.warning("instagram_permalink_fetch_failed", error=str(exc))

        log.info(
            "instagram_upload_done", media_id=media_id, permalink=permalink
        )
        return UploadResult(
            external_id=str(media_id),
            external_url=permalink,
            raw=publish_body,
        )
