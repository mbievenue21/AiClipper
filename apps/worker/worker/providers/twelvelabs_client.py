"""TwelveLabs v1.3 client — assets, index-content, analyze, search."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx
import structlog

from ..analyze.twelvelabs_convert import (
    deduplicate_visual_segments,
    offset_segment_timestamps,
    parse_marengo_search_hits,
    parse_pegasus_segments,
)
from ..analyze.twelvelabs_prompts import (
    build_twelvelabs_search_queries,
    build_twelvelabs_segmentation_prompt,
    pegasus_response_schema,
)
from ..config import Settings, get_settings
from .twelvelabs_assets import TwelveLabsAssetClient
from .twelvelabs_types import (
    ExternalIndexResult,
    ExternalIndexStatus,
    TwelveLabsPromptContext,
    VisualSegmentResult,
)

log = structlog.get_logger(__name__)

API_BASE = "https://api.twelvelabs.io/v1.3"
PROVIDER = "twelvelabs"
# Windows longer than this use async /analyze/tasks (sync HTTP read times out on long VODs).
SYNC_ANALYZE_MAX_SECONDS = 600.0
# TwelveLabs measures uploaded asset duration slightly below local ffprobe.
END_TIME_SAFETY_SECONDS = 0.5


@dataclass
class AnalysisChunk:
    chunk_index: int
    start_seconds: float
    end_seconds: float


class TwelveLabsClient:
    """TwelveLabs Video Understanding Platform client (v1.3 APIs)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._http = httpx.Client(
            base_url=API_BASE,
            timeout=httpx.Timeout(120.0, connect=30.0),
            headers=self._headers(),
        )
        self._assets = TwelveLabsAssetClient(
            request=self._request_with_retry,
            raw_put=self._raw_put,
        )

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"accept": "application/json"}
        if self.settings.twelvelabs_api_key:
            headers["x-api-key"] = self.settings.twelvelabs_api_key
        return headers

    def enabled(self) -> bool:
        return bool(self.settings.twelvelabs_enabled)

    def configured(self) -> bool:
        return bool(
            self.settings.twelvelabs_enabled
            and self.settings.twelvelabs_api_key
            and self.settings.twelvelabs_index_id
        )

    @staticmethod
    def file_sha256(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def compute_chunks(
        self,
        duration_seconds: float,
        *,
        max_chunk_seconds: float | None = None,
    ) -> list[AnalysisChunk]:
        max_chunk = float(
            max_chunk_seconds
            if max_chunk_seconds is not None
            else self.settings.twelvelabs_max_analyze_chunk_seconds
        )
        overlap = float(self.settings.twelvelabs_chunk_overlap_seconds)
        if duration_seconds <= 0 or duration_seconds <= max_chunk:
            return [AnalysisChunk(0, 0.0, max(duration_seconds, 0.0))]

        chunks: list[AnalysisChunk] = []
        start = 0.0
        idx = 0
        while start < duration_seconds:
            end = min(duration_seconds, start + max_chunk)
            chunks.append(AnalysisChunk(idx, start, end))
            if end >= duration_seconds:
                break
            start = max(0.0, end - overlap)
            idx += 1
        return chunks

    def ensure_index(
        self,
        *,
        project_id: str,
        video_path: Path,
        existing_task_id: str | None = None,
        existing_video_id: str | None = None,
        source_sha256: str | None = None,
        chunk_index: int = 0,
        chunk_start_seconds: float = 0.0,
        chunk_end_seconds: float | None = None,
        should_stop: "Callable[[], bool] | None" = None,
    ) -> ExternalIndexResult:
        """Upload chunk as asset and index it (v1.3 assets + index-content APIs)."""
        index_id = self.settings.twelvelabs_index_id
        if not index_id:
            raise ValueError("TWELVELABS_INDEX_ID is required when TwelveLabs is enabled")

        if existing_video_id and existing_task_id and self.settings.twelvelabs_reuse_existing_index:
            log.info(
                "twelvelabs_reuse_index",
                project_id=project_id,
                indexed_asset_id=existing_video_id,
                asset_id=existing_task_id,
            )
            return ExternalIndexResult(
                provider=PROVIDER,
                provider_index_id=index_id,
                provider_video_id=existing_video_id,
                provider_task_id=existing_task_id,
                status="ready",
                metadata={"reused": True, "asset_id": existing_task_id},
            )

        if not video_path.exists():
            raise FileNotFoundError(f"Video not found: {video_path}")

        size_bytes = video_path.stat().st_size
        log.info(
            "twelvelabs_upload_start",
            project_id=project_id,
            path=str(video_path),
            size_mb=round(size_bytes / (1024 * 1024), 2),
            chunk_index=chunk_index,
            chunk_start=round(chunk_start_seconds, 2),
            chunk_end=round(chunk_end_seconds, 2) if chunk_end_seconds else None,
            api="assets+indexed-assets",
        )

        user_metadata: dict[str, Any] = {
            "project_id": project_id,
            "sha256": source_sha256 or "",
            "chunk_index": chunk_index,
            "chunk_start_seconds": chunk_start_seconds,
        }
        if chunk_end_seconds is not None:
            user_metadata["chunk_end_seconds"] = chunk_end_seconds

        asset_id = self._assets.upload_video_asset(
            video_path,
            user_metadata=user_metadata,
            should_stop=should_stop,
        )
        self._assets.wait_asset_ready(asset_id, should_stop=should_stop)

        indexed = self._assets.create_indexed_asset(
            index_id,
            asset_id,
            user_metadata=user_metadata,
        )
        indexed_asset_id = str(indexed.get("_id") or indexed.get("id") or "")
        if not indexed_asset_id:
            raise RuntimeError("TwelveLabs index-content did not return indexed asset id")

        self._assets.wait_indexed_asset_ready(
            index_id, indexed_asset_id, should_stop=should_stop
        )

        return ExternalIndexResult(
            provider=PROVIDER,
            provider_index_id=index_id,
            provider_video_id=indexed_asset_id,
            provider_task_id=asset_id,
            status="ready",
            metadata={
                "asset_id": asset_id,
                "indexed_asset_id": indexed_asset_id,
                "upload_api": "v1.3_assets",
            },
        )

    def wait_for_index_ready(
        self,
        provider_task_id: str,
        *,
        timeout_seconds: float = 3600.0,
        poll_interval: float = 8.0,
    ) -> ExternalIndexStatus:
        """Legacy shim — v1.3 ensure_index blocks until ready."""
        return ExternalIndexStatus(status="ready", provider_video_id=provider_task_id)

    def _get_asset_duration_seconds(self, asset_id: str) -> float | None:
        """Best-effort duration from TwelveLabs asset metadata."""
        if not asset_id:
            return None
        try:
            resp = self._request_with_retry("GET", f"/assets/{asset_id}")
        except Exception as exc:
            log.warning(
                "twelvelabs_asset_duration_lookup_failed",
                asset_id=asset_id,
                error=str(exc)[:200],
            )
            return None
        duration = self._parse_asset_duration_seconds(resp)
        if duration is not None:
            log.info(
                "twelvelabs_asset_duration",
                asset_id=asset_id,
                duration_seconds=round(duration, 3),
            )
        return duration

    @staticmethod
    def _parse_asset_duration_seconds(payload: dict[str, Any]) -> float | None:
        for key in ("duration", "video_duration", "duration_seconds"):
            val = payload.get(key)
            if isinstance(val, (int, float)) and val > 0:
                return float(val)
        for nested in ("system_metadata", "metadata", "video", "asset"):
            block = payload.get(nested)
            if isinstance(block, dict):
                parsed = TwelveLabsClient._parse_asset_duration_seconds(block)
                if parsed is not None:
                    return parsed
        return None

    @staticmethod
    def _clamp_analyze_end(
        start_seconds: float,
        end_seconds: float,
        max_duration_seconds: float | None,
    ) -> float:
        if end_seconds <= start_seconds:
            return end_seconds
        cap = end_seconds
        if max_duration_seconds is not None and max_duration_seconds > 0:
            cap = min(
                cap,
                max(0.0, max_duration_seconds - END_TIME_SAFETY_SECONDS),
            )
        return max(start_seconds + 0.1, cap)

    def segment_highlights(
        self,
        asset_id: str,
        context: TwelveLabsPromptContext,
        *,
        chunk: AnalysisChunk | None = None,
        asset_duration_seconds: float | None = None,
    ) -> list[VisualSegmentResult]:
        """Pegasus 1.5 structured segmentation via sync or async analyze APIs."""
        prompt = build_twelvelabs_segmentation_prompt(context)
        model = self.settings.twelvelabs_model_pegasus
        window_seconds = 0.0
        if chunk and chunk.end_seconds > chunk.start_seconds:
            window_seconds = chunk.end_seconds - chunk.start_seconds

        body: dict[str, Any] = {
            "model_name": model,
            "video": {"type": "asset_id", "asset_id": asset_id},
            "prompt_v2": {"input_text": prompt},
            "analysis_mode": "general",
            "response_format": {
                "type": "json_schema",
                "json_schema": pegasus_response_schema(),
            },
            "temperature": 0.2,
        }
        if chunk and chunk.end_seconds > chunk.start_seconds:
            duration_cap = asset_duration_seconds
            if duration_cap is None:
                duration_cap = self._get_asset_duration_seconds(asset_id)
            clamped_end = self._clamp_analyze_end(
                chunk.start_seconds,
                chunk.end_seconds,
                duration_cap,
            )
            if clamped_end < chunk.end_seconds - 0.01:
                log.info(
                    "twelvelabs_end_time_clamped",
                    requested_end=round(chunk.end_seconds, 3),
                    clamped_end=round(clamped_end, 3),
                    asset_duration=round(duration_cap, 3) if duration_cap else None,
                )
            body["start_time"] = chunk.start_seconds
            body["end_time"] = clamped_end
            window_seconds = clamped_end - chunk.start_seconds

        use_sync = window_seconds <= 0 or window_seconds <= SYNC_ANALYZE_MAX_SECONDS
        if use_sync:
            body["stream"] = False
            read_timeout = min(1800.0, max(300.0, window_seconds * 0.4 + 120.0))
            result = self._request_with_retry(
                "POST", "/analyze", json=body, timeout=read_timeout
            )
            parsed = self._extract_analyze_payload(result)
        else:
            task = self._request_with_retry(
                "POST", "/analyze/tasks", json=body, timeout=60.0
            )
            task_id = str(task.get("task_id") or task.get("_id") or task.get("id") or "")
            if not task_id:
                raise RuntimeError("TwelveLabs async analyze task missing id")
            result = self._wait_analyze_task(task_id)
            parsed = self._extract_analyze_payload(result)

        segments = parse_pegasus_segments(
            parsed,
            model=model,
            chunk_offset=chunk.start_seconds if chunk else 0.0,
            min_confidence=self.settings.twelvelabs_min_visual_confidence,
        )
        log.info(
            "twelvelabs_segmentation_done",
            count=len(segments),
            chunk_index=chunk.chunk_index if chunk else 0,
            mode="sync" if use_sync else "async",
        )
        return segments

    def search_moments(
        self,
        indexed_asset_id: str,
        queries: list[str],
        *,
        duration_seconds: float,
        chunk: AnalysisChunk | None = None,
        asset_duration_seconds: float | None = None,
        return_raw_hit_count: bool = False,
    ) -> list[VisualSegmentResult] | tuple[list[VisualSegmentResult], int]:
        """Marengo semantic search (multipart /search)."""
        index_id = self.settings.twelvelabs_index_id
        if not index_id:
            return ([], 0) if return_raw_hit_count else []

        all_hits: list[VisualSegmentResult] = []
        raw_hit_count = 0
        model = self.settings.twelvelabs_model_marengo
        limit = self.settings.twelvelabs_max_search_results_per_query

        for query in queries:
            filter_obj: dict[str, Any] = {"id": [indexed_asset_id]}
            if chunk and chunk.end_seconds > chunk.start_seconds:
                chunk_end = self._clamp_analyze_end(
                    chunk.start_seconds,
                    chunk.end_seconds,
                    asset_duration_seconds,
                )
                filter_obj["duration"] = {
                    "gte": chunk.start_seconds,
                    "lte": chunk_end,
                }

            multipart: list[tuple[str, tuple[None, str]]] = [
                ("index_id", (None, index_id)),
                ("query_text", (None, query)),
                ("search_options", (None, "visual")),
                ("search_options", (None, "audio")),
                ("operator", (None, "or")),
                ("group_by", (None, "clip")),
                ("page_limit", (None, str(limit))),
                ("filter", (None, json.dumps(filter_obj))),
            ]

            try:
                resp = self._request_with_retry("POST", "/search", files=multipart)
            except Exception as exc:
                log.warning("twelvelabs_search_failed", query=query[:80], error=str(exc))
                continue

            raw_items = resp.get("data") or resp.get("clips") or resp.get("search_results") or []
            if isinstance(raw_items, dict):
                raw_items = raw_items.get("data") or []
            if isinstance(raw_items, list):
                raw_hit_count += len(raw_items)

            hits = parse_marengo_search_hits(
                resp,
                query=query,
                model=model,
                duration_seconds=duration_seconds,
                min_confidence=self.settings.twelvelabs_min_visual_confidence,
            )
            all_hits.extend(hits)

        deduped = deduplicate_visual_segments(all_hits)
        log.info(
            "twelvelabs_search_done",
            queries=len(queries),
            raw_hits=raw_hit_count,
            hits=len(deduped),
        )
        capped = deduped[: self.settings.twelvelabs_visual_candidate_limit]
        if return_raw_hit_count:
            return capped, raw_hit_count
        return capped

    def analyze_video(
        self,
        asset_id: str,
        indexed_asset_id: str,
        context: TwelveLabsPromptContext,
    ) -> list[VisualSegmentResult]:
        """Run Pegasus + Marengo across analyze windows."""
        chunks = self.compute_chunks(context.duration_seconds)
        all_segments: list[VisualSegmentResult] = []
        queries = build_twelvelabs_search_queries(context.vibe)

        for chunk in chunks:
            try:
                pegasus = self.segment_highlights(asset_id, context, chunk=chunk)
                all_segments.extend(pegasus)
            except Exception as exc:
                log.warning(
                    "twelvelabs_pegasus_chunk_failed",
                    chunk=chunk.chunk_index,
                    error=str(exc),
                )
                if not self.settings.twelvelabs_fail_open:
                    raise

            try:
                marengo = self.search_moments(
                    indexed_asset_id,
                    queries,
                    duration_seconds=context.duration_seconds,
                    chunk=chunk,
                )
                all_segments.extend(marengo)
            except Exception as exc:
                log.warning(
                    "twelvelabs_marengo_chunk_failed",
                    chunk=chunk.chunk_index,
                    error=str(exc),
                )
                if not self.settings.twelvelabs_fail_open:
                    raise

        return deduplicate_visual_segments(all_segments)[
            : self.settings.twelvelabs_visual_candidate_limit
        ]

    def analyze_uploaded_chunk(
        self,
        *,
        asset_id: str,
        indexed_asset_id: str,
        context: TwelveLabsPromptContext,
        vod_chunk_start: float,
        vod_chunk_end: float,
        upload_chunk_index: int = 0,
    ) -> tuple[list[VisualSegmentResult], dict[str, Any]]:
        """Analyze one uploaded slice; map timestamps to full-VOD time."""
        chunk_duration = max(0.0, vod_chunk_end - vod_chunk_start)
        diagnostics: dict[str, Any] = {
            "pegasusWindows": 0,
            "pegasusSegments": 0,
            "pegasusErrors": [],
            "marengoQueries": 0,
            "marengoRawHits": 0,
            "marengoSegments": 0,
            "assetDurationSeconds": None,
        }
        if chunk_duration <= 0:
            return [], diagnostics

        asset_duration = self._get_asset_duration_seconds(asset_id)
        diagnostics["assetDurationSeconds"] = asset_duration
        effective_duration = chunk_duration
        if asset_duration is not None:
            effective_duration = min(chunk_duration, asset_duration)

        chunk_audio_peaks = [
            t - vod_chunk_start
            for t in context.audio_peak_times
            if vod_chunk_start <= t < vod_chunk_end
        ]
        chunk_chat_peaks = [
            t - vod_chunk_start
            for t in context.chat_peak_times
            if vod_chunk_start <= t < vod_chunk_end
        ]

        chunk_context = TwelveLabsPromptContext(
            vibe=context.vibe,
            language=context.language,
            transcript_summary=context.transcript_summary,
            duration_seconds=effective_duration,
            audio_peak_times=chunk_audio_peaks,
            chat_peak_times=chunk_chat_peaks,
        )
        pegasus_max = float(self.settings.twelvelabs_pegasus_chunk_seconds)
        analyze_windows = self.compute_chunks(
            effective_duration, max_chunk_seconds=pegasus_max
        )
        diagnostics["pegasusWindows"] = len(analyze_windows)
        all_segments: list[VisualSegmentResult] = []
        queries = build_twelvelabs_search_queries(context.vibe)
        diagnostics["marengoQueries"] = len(queries)

        for window in analyze_windows:
            try:
                pegasus = self.segment_highlights(
                    asset_id,
                    chunk_context,
                    chunk=window,
                    asset_duration_seconds=asset_duration,
                )
                diagnostics["pegasusSegments"] += len(pegasus)
                all_segments.extend(offset_segment_timestamps(pegasus, vod_chunk_start))
            except Exception as exc:
                err = str(exc)[:500]
                diagnostics["pegasusErrors"].append(
                    {"window": window.chunk_index, "error": err}
                )
                log.warning(
                    "twelvelabs_pegasus_upload_chunk_failed",
                    upload_chunk_index=upload_chunk_index,
                    window=window.chunk_index,
                    error=err,
                )
                if not self.settings.twelvelabs_fail_open:
                    raise

            try:
                marengo, raw_hits = self.search_moments(
                    indexed_asset_id,
                    queries,
                    duration_seconds=effective_duration,
                    chunk=window,
                    asset_duration_seconds=asset_duration,
                    return_raw_hit_count=True,
                )
                diagnostics["marengoRawHits"] += raw_hits
                diagnostics["marengoSegments"] += len(marengo)
                all_segments.extend(offset_segment_timestamps(marengo, vod_chunk_start))
            except Exception as exc:
                log.warning(
                    "twelvelabs_marengo_upload_chunk_failed",
                    upload_chunk_index=upload_chunk_index,
                    window=window.chunk_index,
                    error=str(exc),
                )
                if not self.settings.twelvelabs_fail_open:
                    raise

        log.info(
            "twelvelabs_upload_chunk_analyzed",
            upload_chunk_index=upload_chunk_index,
            vod_start=round(vod_chunk_start, 2),
            vod_end=round(vod_chunk_end, 2),
            segments=len(all_segments),
            pegasus_windows=len(analyze_windows),
            pegasus_errors=len(diagnostics["pegasusErrors"]),
            marengo_raw_hits=diagnostics["marengoRawHits"],
        )
        return all_segments, diagnostics

    def delete_or_cleanup(self, indexed_asset_id: str) -> None:
        """Best-effort cleanup of indexed asset — non-fatal."""
        index_id = self.settings.twelvelabs_index_id
        if not index_id or not indexed_asset_id:
            return
        try:
            self._assets.delete_indexed_asset(index_id, indexed_asset_id)
        except Exception as exc:
            log.info("twelvelabs_cleanup_skipped", error=str(exc))

    def _wait_analyze_task(
        self,
        task_id: str,
        *,
        timeout_seconds: float = 7200.0,
        poll_interval: float = 6.0,
    ) -> dict[str, Any]:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            resp = self._request_with_retry("GET", f"/analyze/tasks/{task_id}")
            status = str(resp.get("status") or "")
            if status in ("ready", "completed", "succeeded"):
                return resp
            if status in ("failed", "error"):
                err = resp.get("error") or {}
                message = (
                    err.get("message")
                    if isinstance(err, dict)
                    else str(err or "analyze task failed")
                )
                raise RuntimeError(str(message))
            time.sleep(poll_interval)
        raise TimeoutError(f"analyze task {task_id} timed out")

    @staticmethod
    def _extract_analyze_payload(result: dict[str, Any]) -> dict[str, Any]:
        """Parse sync/async Pegasus JSON from v1.3 analyze responses."""
        if "result" in result and isinstance(result["result"], dict):
            result = result["result"]
        data = result.get("data")
        if isinstance(data, dict):
            return data
        if isinstance(data, str) and data.strip():
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                return {"segments": []}
        for key in ("output", "response"):
            val = result.get(key)
            if isinstance(val, dict):
                return val
            if isinstance(val, str):
                try:
                    return json.loads(val)
                except json.JSONDecodeError:
                    pass
        return result if isinstance(result, dict) else {"segments": []}

    def _raw_put(
        self,
        url: str,
        *,
        content: bytes,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        return httpx.put(
            url,
            content=content,
            headers=headers or {},
            timeout=httpx.Timeout(600.0, connect=30.0),
        )

    def _request_with_retry(
        self,
        method: str,
        path: str,
        *,
        max_attempts: int = 3,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        delay = 2.0
        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                resp = self._http.request(
                    method,
                    path,
                    timeout=timeout if timeout is not None else self._http.timeout,
                    **kwargs,
                )
                if resp.status_code >= 400:
                    raise RuntimeError(
                        f"TwelveLabs {method} {path} failed: {resp.status_code} {resp.text[:400]}"
                    )
                if not resp.content:
                    return {}
                data = resp.json()
                return data if isinstance(data, dict) else {"data": data}
            except Exception as exc:
                last_exc = exc
                log.warning(
                    "twelvelabs_request_retry",
                    method=method,
                    path=path,
                    attempt=attempt,
                    error=str(exc),
                )
                if attempt < max_attempts:
                    time.sleep(delay)
                    delay *= 2
        raise RuntimeError(str(last_exc) if last_exc else "TwelveLabs request failed")

    def close(self) -> None:
        self._http.close()
