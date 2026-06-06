"""TwelveLabs analyze job — Pegasus segmentation + Marengo search."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import delete, select

from ..analyze.chat_features import iter_existing_events, parse_twitch_chat
from ..analyze.signal_peaks import collect_signal_peak_times
from ..analyze.twelvelabs_convert import deduplicate_visual_segments
from ..analyze.twelvelabs_prompts import build_twelvelabs_search_queries
from ..config import get_settings
from ..db import session_scope
from ..pipeline_report import merge_flow_report
from ..models import (
    ChatEvent,
    ExternalVideoIndex,
    HighlightCandidate,
    Project,
    Transcript,
    Video,
    VisualSegment,
)
from ..providers.twelvelabs_client import TwelveLabsClient
from ..providers.twelvelabs_types import TwelveLabsPromptContext, VisualSegmentResult
from . import queue
from .handlers import ProgressReporter, register
from .pipeline_enqueue import forward_payload

log = structlog.get_logger(__name__)


def _media_abs(rel_path: str) -> Path:
    return (get_settings().media_root_path / rel_path).resolve()


@register("twelvelabs_analyze")
async def handle_twelvelabs_analyze(job, progress: ProgressReporter) -> dict[str, Any]:
    payload = job.payload
    project_id = payload.get("project_id") or job.project_id
    video_id = payload.get("video_id")
    reanalysis_mode = str(payload.get("reanalysis_mode") or "full")
    if not project_id or not video_id:
        raise ValueError("twelvelabs_analyze requires project_id and video_id")

    settings = get_settings()
    client = TwelveLabsClient(settings)

    if not client.configured():
        log.info("twelvelabs_analyze_skipped", project_id=project_id)
        merge_flow_report(
            project_id,
            stage="twelvelabs_analyze",
            data={
                "status": "skipped",
                "skipped": True,
                "reason": "not_configured",
            },
            pipeline_run_id=str(payload.get("pipeline_run_id") or "") or None,
        )
        next_job = queue.enqueue(
            "analyze",
            forward_payload(
                payload,
                project_id=project_id,
                video_id=video_id,
                **{
                    k: v
                    for k, v in payload.items()
                    if k.startswith("analyze_") or k.endswith("_override")
                },
            ),
            project_id=project_id,
        )
        return {"skipped": True, "next_job_id": next_job.id}

    with session_scope() as session:
        index_rows = (
            session.execute(
                select(ExternalVideoIndex)
                .where(
                    ExternalVideoIndex.project_id == project_id,
                    ExternalVideoIndex.video_id == video_id,
                    ExternalVideoIndex.provider == "twelvelabs",
                    ExternalVideoIndex.status == "ready",
                )
                .order_by(ExternalVideoIndex.chunk_index)
            )
            .scalars()
            .all()
        )
        index_rows = [
            r for r in index_rows if r.provider_video_id and r.provider_task_id
        ]

        if not index_rows:
            log.warning("twelvelabs_analyze_no_ready_index", project_id=project_id)
            merge_flow_report(
                project_id,
                stage="twelvelabs_analyze",
                data={
                    "status": "skipped",
                    "skipped": True,
                    "reason": "index_not_ready",
                },
                pipeline_run_id=str(payload.get("pipeline_run_id") or "") or None,
            )
            next_job = queue.enqueue(
                "analyze",
                forward_payload(payload, project_id=project_id, video_id=video_id),
                project_id=project_id,
            )
            return {"skipped": True, "reason": "index_not_ready", "next_job_id": next_job.id}

        video = session.get(Video, video_id)
        project = session.get(Project, project_id)
        transcript = session.execute(
            select(Transcript).where(Transcript.video_id == video_id)
        ).scalar_one_or_none()
        duration = float(
            (video.duration_seconds if video else 0.0)
            or index_rows[0].duration_seconds
            or 0.0
        )
        vibe = str(project.settings.get("vibe", "") or "") if project else ""
        min_clip_seconds = float(project.settings.get("minClipSeconds", 20) if project else 20)
        language = transcript.language if transcript else None
        summary = (transcript.full_text or "")[:1200] if transcript else None
        audio_rel = video.audio_path if video else None
        chat_rel = video.chat_json_path if video else None
        settings_snapshot = project.settings if project else {}

        chat_events = list(
            iter_existing_events(
                session.execute(
                    select(ChatEvent)
                    .where(ChatEvent.video_id == video_id)
                    .order_by(ChatEvent.timestamp_seconds)
                )
                .scalars()
                .all()
            )
        )
        if not chat_events and chat_rel:
            chat_abs = _media_abs(chat_rel)
            if chat_abs.exists():
                chat_events = parse_twitch_chat(chat_abs)

        if reanalysis_mode in ("visual_only", "full"):
            session.execute(
                delete(VisualSegment).where(
                    VisualSegment.project_id == project_id,
                    VisualSegment.video_id == video_id,
                    VisualSegment.provider == "twelvelabs",
                )
            )
            session.execute(
                delete(HighlightCandidate).where(
                    HighlightCandidate.project_id == project_id,
                    HighlightCandidate.video_id == video_id,
                    HighlightCandidate.source.in_(
                        ["twelvelabs_pegasus", "twelvelabs_marengo", "twelvelabs_visual_hybrid"]
                    ),
                )
            )

    audio_peak_times: list[float] = []
    chat_peak_times: list[float] = []
    if audio_rel:
        audio_abs = _media_abs(audio_rel)
        if audio_abs.exists():
            audio_peak_times, chat_peak_times = collect_signal_peak_times(
                audio_path=audio_abs,
                chat_events=chat_events,
                duration_seconds=duration,
                min_clip_seconds=min_clip_seconds,
            )

    context = TwelveLabsPromptContext(
        vibe=vibe,
        language=language,
        transcript_summary=summary,
        duration_seconds=duration,
        audio_peak_times=audio_peak_times,
        chat_peak_times=chat_peak_times,
    )

    total_chunks = len(index_rows)
    all_segments: list[VisualSegmentResult] = []
    chunk_diagnostics: list[dict[str, Any]] = []

    try:
        for i, row in enumerate(index_rows):
            frac = 0.1 + 0.75 * (i / max(total_chunks, 1))
            chunk_start = float(row.chunk_start_seconds or 0.0)
            chunk_end = float(
                row.chunk_end_seconds
                if row.chunk_end_seconds is not None
                else duration
            )
            progress(
                frac,
                f"TwelveLabs analyze chunk {i + 1}/{total_chunks} "
                f"({chunk_start:.0f}s–{chunk_end:.0f}s)",
            )
            asset_id = _resolve_asset_id(row)
            chunk_segments, diag = await asyncio.to_thread(
                client.analyze_uploaded_chunk,
                asset_id=asset_id,
                indexed_asset_id=row.provider_video_id,
                context=context,
                vod_chunk_start=chunk_start,
                vod_chunk_end=chunk_end,
                upload_chunk_index=int(row.chunk_index or i),
            )
            chunk_diagnostics.append(diag)
            all_segments.extend(chunk_segments)
    except Exception as exc:
        log.exception("twelvelabs_analyze_failed", project_id=project_id)
        merge_flow_report(
            project_id,
            stage="twelvelabs_analyze",
            data={
                "status": "failed",
                "failedOpen": settings.twelvelabs_fail_open,
                "error": str(exc)[:500],
                "peaksFed": {
                    "audioPeakCount": len(audio_peak_times),
                    "chatPeakCount": len(chat_peak_times),
                    "audioPeakSample": audio_peak_times[:5],
                    "chatPeakSample": chat_peak_times[:5],
                },
                "vibeUsed": vibe,
            },
            pipeline_run_id=str(payload.get("pipeline_run_id") or "") or None,
            settings_snapshot=settings_snapshot,
        )
        if settings.twelvelabs_fail_open:
            next_job = queue.enqueue(
                "analyze",
                forward_payload(payload, project_id=project_id, video_id=video_id),
                project_id=project_id,
            )
            return {"failed_open": True, "error": str(exc)[:500], "next_job_id": next_job.id}
        raise

    segments = deduplicate_visual_segments(all_segments)[
        : settings.twelvelabs_visual_candidate_limit
    ]
    pegasus_count = sum(1 for s in segments if s.source_method != "marengo_search")
    marengo_count = sum(1 for s in segments if s.source_method == "marengo_search")
    progress(0.9, "saving visual segments")
    _persist_segments_and_candidates(project_id, video_id, segments)

    pegasus_errors = [
        err
        for diag in chunk_diagnostics
        for err in (diag.get("pegasusErrors") or [])
    ]
    marengo_raw_hits = sum(
        int(diag.get("marengoRawHits") or 0) for diag in chunk_diagnostics
    )
    pegasus_windows = sum(
        int(diag.get("pegasusWindows") or 0) for diag in chunk_diagnostics
    )

    merge_flow_report(
        project_id,
        stage="twelvelabs_analyze",
        data={
            "status": "ok" if len(segments) > 0 else "empty",
            "visualSegmentCount": len(segments),
            "pegasusCount": pegasus_count,
            "marengoCount": marengo_count,
            "uploadChunkCount": total_chunks,
            "pegasusWindowCount": pegasus_windows,
            "pegasusErrors": pegasus_errors[:5],
            "marengoRawHits": marengo_raw_hits,
            "marengoQueryCount": len(build_twelvelabs_search_queries(vibe)),
            "minVisualConfidence": settings.twelvelabs_min_visual_confidence,
            "peaksFed": {
                "audioPeakCount": len(audio_peak_times),
                "chatPeakCount": len(chat_peak_times),
                "audioPeakSample": audio_peak_times[:5],
                "chatPeakSample": chat_peak_times[:5],
            },
            "vibeUsed": vibe,
            "transcriptSummaryChars": len(summary or ""),
        },
        pipeline_run_id=str(payload.get("pipeline_run_id") or "") or None,
        settings_snapshot=settings_snapshot,
    )

    analyze_payload = forward_payload(
        payload,
        project_id=project_id,
        video_id=video_id,
        twelvelabs_used=True,
        upload_chunk_count=total_chunks,
    )
    for key in ("analyze_model_override", "vibe_override", "reanalysis_mode"):
        if key in payload:
            analyze_payload[key] = payload[key]

    next_job = queue.enqueue("analyze", analyze_payload, project_id=project_id)
    progress(1.0, "TwelveLabs analysis complete")
    log.info(
        "twelvelabs_analyze_done",
        project_id=project_id,
        visual_segments=len(segments),
        upload_chunks=total_chunks,
    )
    return {
        "visual_segment_count": len(segments),
        "upload_chunk_count": total_chunks,
        "next_job_id": next_job.id,
    }


def _resolve_asset_id(row: ExternalVideoIndex) -> str:
    if row.provider_task_id:
        return row.provider_task_id
    if row.metadata_json:
        try:
            meta = json.loads(row.metadata_json)
        except json.JSONDecodeError:
            meta = {}
        asset_id = meta.get("asset_id")
        if asset_id:
            return str(asset_id)
    raise ValueError(
        f"TwelveLabs index row {row.id} missing asset_id — re-run twelvelabs_index"
    )


def _persist_segments_and_candidates(
    project_id: str,
    video_id: str,
    segments: list[VisualSegmentResult],
) -> None:
    with session_scope() as session:
        for seg in segments:
            vs = VisualSegment(
                project_id=project_id,
                video_id=video_id,
                provider=seg.provider,
                model=seg.model,
                source_method=seg.source_method,
                start_seconds=seg.start_seconds,
                end_seconds=seg.end_seconds,
                segment_type=seg.segment_type,
                confidence=seg.confidence,
                title=seg.title,
                description=seg.description,
                visual_reason=seg.visual_reason,
                audio_reason=seg.audio_reason,
                speech_reason=seg.speech_reason,
                raw_json=json.dumps(seg.raw) if seg.raw else None,
            )
            session.add(vs)

            source = (
                "twelvelabs_marengo"
                if seg.source_method == "marengo_search"
                else "twelvelabs_pegasus"
            )
            start = seg.suggested_clip_start_seconds or seg.start_seconds
            end = seg.suggested_clip_end_seconds or seg.end_seconds
            hc = HighlightCandidate(
                project_id=project_id,
                video_id=video_id,
                source=source,
                start_seconds=start,
                end_seconds=end,
                seed_source=source,
                moment_type=seg.segment_type,
                confidence=seg.confidence,
                score=seg.confidence,
                visual_score=seg.confidence,
                visual_peak_at=(start + end) / 2.0,
                title=seg.title,
                summary=seg.description,
                reason_json=json.dumps(
                    {
                        "twelvelabs": {
                            "segment_type": seg.segment_type,
                            "confidence": seg.confidence,
                            "visual_reason": seg.visual_reason,
                            "source_method": seg.source_method,
                        }
                    }
                ),
                raw_provider_json=json.dumps(seg.raw) if seg.raw else None,
            )
            session.add(hc)
