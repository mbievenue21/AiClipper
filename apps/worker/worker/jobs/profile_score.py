"""Profile scoring job — explainable signal breakdown per candidate."""

from __future__ import annotations

import json
import time
from typing import Any

import structlog
from sqlalchemy import delete, select

from ..db import session_scope
from ..models import ExtractedFeatureWindow, ProfileScoredCandidate, Project
from ..pipeline_report import merge_flow_report
from ..pipeline_timing import ensure_run_id, record_stage
from ..profile.loader import load_active_profile_version
from ..profile.project_context import load_project_context
from ..profile.ranker import load_ranker
from ..profile.scorer import score_candidates
from ..analyze.audio_features import compute_audio_features
from ..analyze.chat_features import compute_chat_density
from .candidate_generate import load_staged_candidates, _staging_path
from .handlers import ProgressReporter, register
from .pipeline_enqueue import forward_payload

log = structlog.get_logger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


@register("profile_score")
async def handle_profile_score(job, progress: ProgressReporter) -> dict[str, Any]:
    payload = job.payload
    project_id = payload.get("project_id") or job.project_id
    if not project_id:
        raise ValueError("profile_score requires project_id")

    pipeline_run_id = ensure_run_id(project_id, payload)
    ctx = load_project_context(project_id)

    profile_version_id = payload.get("profile_version_id")
    if profile_version_id:
        active = load_active_profile_version(
            str(ctx.settings.get("highlightProfileId") or "")
        )
        if active.version_id != profile_version_id:
            active = load_active_profile_version(profile_version_id)
    else:
        active = load_active_profile_version(
            str(ctx.settings.get("highlightProfileId") or "") or None
        )

    candidates = load_staged_candidates(project_id)
    if not candidates:
        raise ValueError("No staged candidates found. Run candidate_generate first.")

    progress(0.3, "scoring candidates with profile")
    audio_series = compute_audio_features(ctx.audio_path)
    chat_density = compute_chat_density(
        ctx.chat_events,
        duration_seconds=ctx.duration_seconds or audio_series.duration_seconds,
    )

    t0 = time.perf_counter()
    ranker = None
    with session_scope() as session:
        from ..models import HighlightProfileVersion

        ver = session.get(HighlightProfileVersion, active.version_id)
        if ver and ver.model_artifact_path:
            ranker = load_ranker(ver.model_type, ver.model_artifact_path)

    scored = score_candidates(
        candidates,
        segments=ctx.segments,
        audio=audio_series,
        chat=chat_density,
        scene_cuts=ctx.scene_cuts,
        config=active.config,
        duration_seconds=ctx.duration_seconds or audio_series.duration_seconds,
        video_path=ctx.video_path,
        vibe=str(ctx.settings.get("vibe", "") or ""),
        ranker=ranker,
    )
    score_ms = int((time.perf_counter() - t0) * 1000)

    progress(0.8, "persisting scored candidates")
    with session_scope() as session:
        session.execute(
            delete(ProfileScoredCandidate).where(
                ProfileScoredCandidate.project_id == project_id
            )
        )
        session.execute(
            delete(ExtractedFeatureWindow).where(
                ExtractedFeatureWindow.project_id == project_id
            )
        )
        for sc in scored:
            row = ProfileScoredCandidate(
                project_id=project_id,
                profile_version_id=active.version_id,
                start_seconds=sc.candidate.start_seconds,
                end_seconds=sc.candidate.end_seconds,
                score=sc.score,
                title_suggestion=sc.title_suggestion,
                explanation=sc.breakdown.explanation,
            )
            row.signal_breakdown_json = json.dumps(sc.breakdown.to_dict())
            session.add(row)

        for sc in scored[:50]:
            if not sc.features:
                continue
            feats = sc.features
            fw = ExtractedFeatureWindow(
                project_id=project_id,
                start_seconds=sc.candidate.start_seconds,
                end_seconds=sc.candidate.end_seconds,
                window_size_seconds=sc.candidate.duration_seconds,
                transcript_text=str(feats.get("transcript_text") or ""),
            )
            fw.features_json = json.dumps(feats)
            fw.audio_features_json = json.dumps(feats.get("audio") or {})
            fw.chat_features_json = json.dumps(feats.get("chat") or {})
            fw.visual_features_json = json.dumps(feats.get("visual") or {})
            session.add(fw)

    staging = _staging_path(project_id)
    if staging.exists():
        staging.unlink()

    record_stage(
        run_id=pipeline_run_id,
        project_id=project_id,
        stage="profile_score",
        duration_ms=score_ms,
        started_at=_now_ms() - score_ms,
        finished_at=_now_ms(),
        status="ok",
        job_id=job.id,
        meta={"scored_count": len(scored)},
    )

    merge_flow_report(
        project_id,
        stage="profile_score",
        data={
            "status": "ok",
            "scoredCount": len(scored),
            "topScore": scored[0].score if scored else 0,
            "profileVersionId": active.version_id,
        },
        pipeline_run_id=pipeline_run_id,
    )

    from . import queue

    queue.enqueue(
        "analyze",
        forward_payload(
            payload,
            project_id=project_id,
            video_id=ctx.video_id,
            profile_version_id=active.version_id,
            profile_scored=True,
        ),
        project_id=project_id,
    )

    progress(1.0, "profile scoring complete")
    return {
        "project_id": project_id,
        "scored_count": len(scored),
        "profile_version_id": active.version_id,
        "pipeline_run_id": pipeline_run_id,
    }
