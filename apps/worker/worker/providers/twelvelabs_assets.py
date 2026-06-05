"""TwelveLabs v1.3 asset upload + index-content APIs (replaces deprecated /tasks)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

import httpx
import structlog

log = structlog.get_logger(__name__)

# TwelveLabs direct upload limit for local files.
DIRECT_UPLOAD_MAX_BYTES = 200 * 1024 * 1024

# Granularity for cooperative cancellation while polling for asset readiness.
# We sleep in small slices and re-check should_stop between them so Ctrl+C and
# project deletion stop the worker within ~1s instead of waiting out the full
# poll interval.
_POLL_TICK_SECONDS = 1.0


class UploadCancelled(Exception):
    """Raised when an in-progress upload is asked to stop (Ctrl+C, project deleted)."""


def _wait_or_cancel(
    seconds: float,
    should_stop: Callable[[], bool] | None,
    *,
    reason: str,
) -> None:
    """time.sleep(seconds) but split into 1s slices so cancellation is fast."""
    if should_stop is None:
        time.sleep(seconds)
        return
    remaining = seconds
    while remaining > 0:
        if should_stop():
            raise UploadCancelled(reason)
        tick = min(_POLL_TICK_SECONDS, remaining)
        time.sleep(tick)
        remaining -= tick


class TwelveLabsAssetClient:
    """Upload videos as assets and index them for Marengo search."""

    def __init__(
        self,
        *,
        request: Callable[..., dict[str, Any]],
        raw_put: Callable[..., httpx.Response],
    ) -> None:
        self._request = request
        self._raw_put = raw_put

    def upload_video_asset(
        self,
        video_path: Path,
        *,
        user_metadata: dict[str, Any] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> str:
        size = video_path.stat().st_size
        if size <= DIRECT_UPLOAD_MAX_BYTES:
            return self._upload_direct(video_path, user_metadata=user_metadata)
        return self._upload_multipart(
            video_path,
            user_metadata=user_metadata,
            should_stop=should_stop,
        )

    def wait_asset_ready(
        self,
        asset_id: str,
        *,
        timeout_seconds: float = 3600.0,
        poll_interval: float = 8.0,
        should_stop: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        deadline = time.time() + timeout_seconds
        last_status = "pending"
        while time.time() < deadline:
            if should_stop and should_stop():
                raise UploadCancelled(f"cancelled while waiting on asset {asset_id}")
            resp = self._request("GET", f"/assets/{asset_id}")
            last_status = str(resp.get("status") or last_status)
            if last_status == "ready":
                return resp
            if last_status == "failed":
                raise RuntimeError(f"TwelveLabs asset {asset_id} processing failed")
            _wait_or_cancel(
                poll_interval,
                should_stop,
                reason=f"cancelled while waiting on asset {asset_id}",
            )
        raise TimeoutError(
            f"TwelveLabs asset {asset_id} not ready after {timeout_seconds:.0f}s "
            f"(last={last_status})"
        )

    def create_indexed_asset(
        self,
        index_id: str,
        asset_id: str,
        *,
        user_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "asset_id": asset_id,
            "enable_video_stream": False,
        }
        if user_metadata:
            body["user_metadata"] = user_metadata
        return self._request(
            "POST",
            f"/indexes/{index_id}/indexed-assets",
            json=body,
        )

    def wait_indexed_asset_ready(
        self,
        index_id: str,
        indexed_asset_id: str,
        *,
        timeout_seconds: float = 3600.0,
        poll_interval: float = 8.0,
        should_stop: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        deadline = time.time() + timeout_seconds
        last_status = "pending"
        while time.time() < deadline:
            if should_stop and should_stop():
                raise UploadCancelled(
                    f"cancelled while waiting on indexed asset {indexed_asset_id}"
                )
            resp = self._request(
                "GET",
                f"/indexes/{index_id}/indexed-assets/{indexed_asset_id}",
            )
            last_status = str(resp.get("status") or last_status)
            if last_status == "ready":
                return resp
            if last_status == "failed":
                raise RuntimeError(
                    f"TwelveLabs indexed asset {indexed_asset_id} failed"
                )
            _wait_or_cancel(
                poll_interval,
                should_stop,
                reason=f"cancelled while waiting on indexed asset {indexed_asset_id}",
            )
        raise TimeoutError(
            f"TwelveLabs indexed asset {indexed_asset_id} not ready after "
            f"{timeout_seconds:.0f}s (last={last_status})"
        )

    def delete_indexed_asset(self, index_id: str, indexed_asset_id: str) -> None:
        self._request(
            "DELETE",
            f"/indexes/{index_id}/indexed-assets/{indexed_asset_id}",
        )

    def _upload_direct(
        self,
        video_path: Path,
        *,
        user_metadata: dict[str, Any] | None = None,
    ) -> str:
        data: dict[str, str] = {"method": "direct", "filename": video_path.name}
        if user_metadata:
            import json

            data["user_metadata"] = json.dumps(user_metadata)
        with video_path.open("rb") as vf:
            resp = self._request(
                "POST",
                "/assets",
                data=data,
                files={"file": (video_path.name, vf, "video/mp4")},
            )
        asset_id = str(resp.get("_id") or resp.get("id") or "")
        if not asset_id:
            raise RuntimeError("TwelveLabs direct asset upload missing asset id")
        log.info(
            "twelvelabs_asset_uploaded_direct",
            asset_id=asset_id,
            size_mb=round(video_path.stat().st_size / (1024 * 1024), 2),
        )
        return asset_id

    def _abort_multipart(self, upload_id: str) -> None:
        """Best-effort abort of an in-progress multipart upload session.

        Called when the worker is shutting down or the job was cancelled so
        TwelveLabs doesn't keep the half-finished asset around forever.
        Swallows all errors — we're already on the failure path.
        """
        try:
            self._request("DELETE", f"/assets/multipart-uploads/{upload_id}")
            log.info("twelvelabs_multipart_upload_aborted", upload_id=upload_id)
        except Exception as exc:
            log.warning(
                "twelvelabs_multipart_abort_failed",
                upload_id=upload_id,
                error=str(exc)[:200],
            )

    def _upload_multipart(
        self,
        video_path: Path,
        *,
        user_metadata: dict[str, Any] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> str:
        size = video_path.stat().st_size
        body: dict[str, Any] = {
            "filename": video_path.name,
            "type": "video",
            "total_size": size,
        }
        if user_metadata:
            body["user_metadata"] = user_metadata

        session = self._request("POST", "/assets/multipart-uploads", json=body)
        upload_id = str(session["upload_id"])
        asset_id = str(session["asset_id"])
        chunk_size = int(session["chunk_size"])
        total_chunks = int(session["total_chunks"])
        upload_headers = session.get("upload_headers") or {}

        url_map: dict[int, str] = {
            int(item["chunk_index"]): str(item["url"])
            for item in (session.get("upload_urls") or [])
        }

        pending_report: list[dict[str, Any]] = []
        reported: set[int] = set()

        def check_cancel() -> None:
            if should_stop and should_stop():
                raise UploadCancelled(
                    f"upload cancelled at part {len(reported)}/{total_chunks}"
                )

        def ensure_url(chunk_index: int) -> str:
            if chunk_index in url_map:
                return url_map[chunk_index]
            start = chunk_index
            count = min(10, total_chunks - chunk_index + 1)
            extra = self._request(
                "POST",
                f"/assets/multipart-uploads/{upload_id}/presigned-urls",
                json={"start": start, "count": count},
            )
            for item in extra.get("upload_urls") or []:
                url_map[int(item["chunk_index"])] = str(item["url"])
            if chunk_index not in url_map:
                raise RuntimeError(
                    f"TwelveLabs missing presigned URL for chunk {chunk_index}"
                )
            return url_map[chunk_index]

        def flush_reports() -> None:
            nonlocal pending_report
            if not pending_report:
                return
            result = self._request(
                "POST",
                f"/assets/multipart-uploads/{upload_id}",
                json={"completed_chunks": pending_report},
            )
            pending_report = []
            total_completed = int(result.get("total_completed") or 0)
            if total_completed >= total_chunks and result.get("asset_id"):
                log.info(
                    "twelvelabs_multipart_upload_complete",
                    asset_id=result.get("asset_id"),
                    total_chunks=total_chunks,
                )

        log.info(
            "twelvelabs_multipart_upload_start",
            asset_id=asset_id,
            upload_id=upload_id,
            total_chunks=total_chunks,
            chunk_size_mb=round(chunk_size / (1024 * 1024), 2),
            size_mb=round(size / (1024 * 1024), 2),
        )

        # Log progress every N parts so the operator can see the upload is
        # actually moving (and roughly how far in we are) without piping
        # one log line per HTTP request.
        log_every = max(1, total_chunks // 20)

        try:
            with video_path.open("rb") as vf:
                for chunk_index in range(1, total_chunks + 1):
                    if chunk_index in reported:
                        continue
                    # Cooperative cancellation point — checked before every
                    # PUT so a Ctrl+C / project-delete unwinds within ~one
                    # part instead of running to completion.
                    check_cancel()

                    start_byte = (chunk_index - 1) * chunk_size
                    vf.seek(start_byte)
                    chunk_bytes = vf.read(
                        chunk_size if chunk_index < total_chunks else size - start_byte
                    )
                    url = ensure_url(chunk_index)
                    put_resp = self._raw_put(
                        url,
                        content=chunk_bytes,
                        headers=upload_headers,
                    )
                    if put_resp.status_code >= 400:
                        raise RuntimeError(
                            f"TwelveLabs chunk {chunk_index} upload failed: "
                            f"{put_resp.status_code} {put_resp.text[:200]}"
                        )
                    etag = (put_resp.headers.get("etag") or "").strip('"')
                    if not etag:
                        raise RuntimeError(
                            f"TwelveLabs chunk {chunk_index} upload missing ETag"
                        )
                    pending_report.append(
                        {
                            "chunk_index": chunk_index,
                            "proof": etag,
                            "proof_type": "etag",
                            "chunk_size": len(chunk_bytes),
                        }
                    )
                    reported.add(chunk_index)
                    if chunk_index % log_every == 0 or chunk_index == total_chunks:
                        log.info(
                            "twelvelabs_multipart_upload_progress",
                            asset_id=asset_id,
                            uploaded=chunk_index,
                            total=total_chunks,
                            pct=round(100.0 * chunk_index / total_chunks, 1),
                        )
                    if len(pending_report) >= 5:
                        flush_reports()

            flush_reports()
        except UploadCancelled:
            # Tell TwelveLabs to release the partial upload and re-raise so
            # the caller can mark the job cancelled instead of failed.
            self._abort_multipart(upload_id)
            raise
        except Exception:
            # Best-effort abort on any unexpected failure too, otherwise the
            # half-uploaded asset lingers and counts against the org quota.
            self._abort_multipart(upload_id)
            raise

        log.info(
            "twelvelabs_asset_uploaded_multipart",
            asset_id=asset_id,
            upload_id=upload_id,
            total_chunks=total_chunks,
            size_mb=round(size / (1024 * 1024), 2),
        )
        return asset_id
