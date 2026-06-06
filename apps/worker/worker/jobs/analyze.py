"""Analyze job handler."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select

from ..analyze import AnalysisInput, analyze_project
from ..analyze.candidates import Segment
from ..analyze.chat_features import iter_existing_events, parse_twitch_chat
from ..config import get_settings
from ..db import session_scope
from ..pipeline_report import build_path_decisions, finalize_flow_report, merge_flow_report
from ..pipeline_timing import complete_run, ensure_run_id, record_stage, record_stages
from ..models import (
    AudioFeatures,
    ChatEvent,
    ChatFeatures,
    ExternalVideoIndex,
    Highlight,
    Project,
    Transcript,
    TranscriptSegment,
    Video,
    VisualSegment,
)
from ..providers.twelvelabs_types import VisualSegmentResult
from .handlers import ProgressReporter, register

log = structlog.get_logger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _set_project_status(session, project: Project, status: str, *, note: str | None = None) -> None:
    project.status = status
    project.updated_at = _now_ms()
    if note is not None:
        project.notes = note


def _media_abs(rel_path: str) -> Path:
    return (get_settings().media_root_path / rel_path).resolve()


@register("analyze")
async def handle_analyze(job, progress: ProgressReporter) -> dict[str, Any]:
    payload = job.payload
    project_id = payload.get("project_id") or job.project_id
    if not project_id:
        raise ValueError("analyze job requires project_id")

    log.info("analyze_start", project_id=project_id)
    pipeline_run_id = ensure_run_id(project_id, payload, partial=bool(payload.get("reanalysis_mode")))

    with session_scope() as session:
        project = session.get(Project, project_id)
        if project is None:
            raise ValueError(f"Project {project_id!r} not found")

        video = session.execute(
            select(Video).where(Video.project_id == project_id)
        ).scalar_one_or_none()
        if video is None or not video.audio_path:
            raise ValueError(
                "analyze requires ingest + transcribe to have completed first."
            )

        transcript = session.execute(
            select(Transcript).where(Transcript.video_id == video.id)
        ).scalar_one_or_none()
        if transcript is None:
            raise ValueError(
                f"No transcript for video {video.id}. Run transcribe first."
            )

        seg_rows = (
            session.execute(
                select(TranscriptSegment)
                .where(TranscriptSegment.transcript_id == transcript.id)
                .order_by(TranscriptSegment.start_seconds)
            )
            .scalars()
            .all()
        )

        existing_events = (
            session.execute(
                select(ChatEvent)
                .where(ChatEvent.video_id == video.id)
                .order_by(ChatEvent.timestamp_seconds)
            )
            .scalars()
            .all()
        )

        settings = project.settings
        audio_rel = video.audio_path
        video_rel = video.file_path
        chat_rel = video.chat_json_path
        duration = float(video.duration_seconds or 0.0)
        language = transcript.language
        video_id = video.id
        existing_audio_features = session.execute(
            select(AudioFeatures).where(AudioFeatures.video_id == video.id)
        ).scalar_one_or_none()
        existing_highlights_count = session.execute(
            select(Highlight).where(Highlight.video_id == video.id)
        ).scalars().all()

        segments = [
            Segment(
                start_seconds=float(s.start_seconds),
                end_seconds=float(s.end_seconds),
                text=s.text or "",
            )
            for s in seg_rows
        ]
        chat_events_internal = iter_existing_events(existing_events)
        already_have_chat_rows = len(chat_events_internal) > 0
        had_audio_features = existing_audio_features is not None
        had_highlights = len(existing_highlights_count) > 0

        visual_rows = (
            session.execute(
                select(VisualSegment)
                .where(VisualSegment.video_id == video_id)
                .order_by(VisualSegment.start_seconds)
            )
            .scalars()
            .all()
        )
        # A large VOD is split into multiple upload chunks, so there can be
        # several ExternalVideoIndex rows per video. Fetch them all (don't use
        # scalar_one_or_none — that raises MultipleResultsFound on >1 chunk).
        twelvelabs_index_rows = (
            session.execute(
                select(ExternalVideoIndex)
                .where(
                    ExternalVideoIndex.project_id == project_id,
                    ExternalVideoIndex.video_id == video_id,
                    ExternalVideoIndex.provider == "twelvelabs",
                )
                .order_by(ExternalVideoIndex.updated_at.desc())
            )
            .scalars()
            .all()
        )
        # Treat the visual index as usable only when every chunk is ready
        # (full timeline coverage).
        twelvelabs_index_ready = bool(twelvelabs_index_rows) and all(
            r.status == "ready" for r in twelvelabs_index_rows
        )

        _set_project_status(session, project, "analyzing")

    visual_segments = [
        VisualSegmentResult(
            provider=row.provider,
            model=row.model or "twelvelabs",
            source_method=row.source_method,
            start_seconds=float(row.start_seconds),
            end_seconds=float(row.end_seconds),
            segment_type=row.segment_type or "visual_payoff",
            confidence=float(row.confidence or 0.5),
            title=row.title,
            description=row.description,
            visual_reason=row.visual_reason,
            audio_reason=row.audio_reason,
            speech_reason=row.speech_reason,
            chat_reason=row.chat_reason,
            raw=json.loads(row.raw_json) if row.raw_json else {},
        )
        for row in visual_rows
    ]
    twelvelabs_used = bool(
        payload.get("twelvelabs_used")
        or visual_segments
        or twelvelabs_index_ready
    )

    new_chat_events_for_db = []
    if not already_have_chat_rows and chat_rel:
        chat_abs = _media_abs(chat_rel)
        if chat_abs.exists():
            new_chat_events_for_db = parse_twitch_chat(chat_abs)
            chat_events_internal = new_chat_events_for_db
            log.info("chat_imported_from_json", count=len(chat_events_internal))

    audio_abs = _media_abs(audio_rel)
    if not audio_abs.exists():
        raise FileNotFoundError(f"audio file missing: {audio_abs}")

    source_video_abs = _media_abs(video_rel) if video_rel else None

    # Accept logical tiers ("pro"/"flash"), legacy IDs ("gemini-2.5-*"), or an
    # explicit model string. gemini.resolve_model() maps these to current IDs.
    analyze_model_override = payload.get("analyze_model_override")
    if analyze_model_override:
        chosen_model = str(analyze_model_override)
    else:
        chosen_model = str(settings.get("analyzeModel") or "flash")

    vibe_override = payload.get("vibe_override")
    chosen_vibe = (
        str(vibe_override).strip()
        if vibe_override is not None
        else str(settings.get("vibe", "") or "")
    )

    analysis_input = AnalysisInput(
        audio_path=audio_abs,
        duration_seconds=duration,
        segments=segments,
        chat_events=chat_events_internal,
        language=language,
        top_n=int(settings.get("topN", 3)),
        min_clip_seconds=float(settings.get("minClipSeconds", 20)),
        max_clip_seconds=float(settings.get("maxClipSeconds", 60)),
        vibe=chosen_vibe,
        pre_roll_seconds=float(settings.get("preRollSeconds", 8)),
        tail_padding_seconds=float(settings.get("tailPaddingSeconds", 2)),
        analyze_model=chosen_model,
        source_video_path=source_video_abs,
        scene_cuts=[],
        visual_segments=visual_segments or None,
        twelvelabs_used=twelvelabs_used,
    )

    try:
        result = await asyncio.to_thread(
            analyze_project, analysis_input, progress=progress
        )
    except Exception as exc:
        log.exception("analyze_failed", project_id=project_id)
        with session_scope() as session:
            project = session.get(Project, project_id)
            if project is not None:
                _set_project_status(session, project, "failed", note=str(exc)[:2000])
        raise

    record_stages(
        run_id=pipeline_run_id,
        project_id=project_id,
        stages=result.stage_timings_ms,
        job_id=job.id,
    )

    progress(0.97, "saving highlights")
    save_started = time.perf_counter()
    save_started_at = _now_ms()
    with session_scope() as session:
        video_row = session.get(Video, video_id)
        if had_audio_features:
            session.execute(
                select(AudioFeatures).where(AudioFeatures.video_id == video_id)
            ).scalar_one().samples = result.audio_series.samples
        else:
            af = AudioFeatures(video_id=video_id)
            af.samples = result.audio_series.samples
            session.add(af)

        # Persist chat density series.
        existing_cf = session.execute(
            select(ChatFeatures).where(ChatFeatures.video_id == video_id)
        ).scalar_one_or_none()
        chat_density_payload = {
            "rawPerSecond": result.chat_density.raw_per_second,
            "normalised": result.chat_density.normalised,
            "totalMessages": result.chat_density.total_messages,
        }
        if existing_cf:
            existing_cf.density_json = json.dumps(chat_density_payload)
        else:
            cf = ChatFeatures(video_id=video_id)
            cf.density_json = json.dumps(chat_density_payload)
            session.add(cf)

        if new_chat_events_for_db:
            for ev in new_chat_events_for_db:
                session.add(
                    ChatEvent(
                        video_id=video_id,
                        timestamp_seconds=ev.timestamp_seconds,
                        username=ev.username,
                        message=ev.message,
                        emote_count=ev.emote_count,
                        message_type=ev.message_type,
                    )
                )

        if had_highlights:
            for h in (
                session.execute(
                    select(Highlight).where(Highlight.video_id == video_id)
                )
                .scalars()
                .all()
            ):
                session.delete(h)
            session.flush()

        for h in result.highlights:
            row = Highlight(
                video_id=video_id,
                start_seconds=h.start_seconds,
                end_seconds=h.end_seconds,
                score=h.score,
                title=h.title,
                summary=h.summary,
                status="candidate",
            )
            row.reason_json = json.dumps(h.to_reason_json())
            session.add(row)

        project = session.get(Project, project_id)
        if project is not None:
            note = None
            if result.notes:
                note = "\n".join(result.notes)[:2000]
            _set_project_status(session, project, "ready", note=note)

    from ..analyze.gemini import resolve_model

    seed_counts = {
        src: sum(1 for c in result.candidates if c.seed_source == src)
        for src in ("transcript", "audio_peak", "chat_peak")
    }
    report = merge_flow_report(
        project_id,
        stage="analyze",
        data={
            "status": "ok",
            "fusionUsed": result.fusion_used,
            "twelvelabsUsed": twelvelabs_used,
            "visualSegmentCount": len(visual_segments),
            "localCandidateCount": result.local_candidate_count,
            "candidateCount": len(result.candidates),
            "highlightCount": len(result.highlights),
            "geminiUsed": result.used_llm,
            "geminiModel": resolve_model(chosen_model) if result.used_llm else None,
            "analyzeModelTier": chosen_model,
            "vibeUsed": chosen_vibe,
            "enrichmentUsed": result.enrichment_used,
            "multimodalUsed": result.multimodal_used,
            "chatAvailable": bool(chat_events_internal),
            "seedCounts": seed_counts,
            "stageTimingsMs": result.stage_timings_ms,
            "notes": result.notes,
        },
        pipeline_run_id=pipeline_run_id,
        settings_snapshot=settings,
    )
    finalize_flow_report(
        project_id,
        decisions=build_path_decisions(report),
        pipeline_run_id=pipeline_run_id,
    )
    complete_run(pipeline_run_id, status="complete")

    record_stage(
        run_id=pipeline_run_id,
        project_id=project_id,
        stage="highlights_save",
        duration_ms=int((time.perf_counter() - save_started) * 1000),
        started_at=save_started_at,
        finished_at=_now_ms(),
        status="ok",
        job_id=job.id,
        meta={"highlight_count": len(result.highlights)},
    )

    progress(1.0, "analyze complete")
    log.info(
        "analyze_done",
        project_id=project_id,
        highlights=len(result.highlights),
        candidates=len(result.candidates),
        used_llm=result.used_llm,
    )

    return {
        "project_id": project_id,
        "video_id": video_id,
        "highlight_count": len(result.highlights),
        "candidate_count": len(result.candidates),
        "used_llm": result.used_llm,
        "analyze_model": resolve_model(chosen_model),
        "twelvelabs_used": twelvelabs_used,
        "visual_segment_count": len(visual_segments),
        "notes": result.notes,
        "pipeline_run_id": pipeline_run_id,
        "stage_timings_ms": result.stage_timings_ms,
    }
