"""Candidate generation job using profile config."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import structlog

from ..config import get_settings
from ..db import session_scope
from ..models import Project
from ..pipeline_report import merge_flow_report
from ..pipeline_timing import ensure_run_id, record_stage
from ..profile.candidates import ProfileCandidate, generate_profile_candidates
from ..profile.loader import load_active_profile_version
from ..profile.project_context import load_project_context
from ..analyze.audio_features import AudioFeatureSeries, compute_audio_features
from ..analyze.chat_features import ChatDensitySeries, compute_chat_density
from .handlers import ProgressReporter, register
from .pipeline_enqueue import forward_payload

log = structlog.get_logger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _staging_path(project_id: str) -> Path:
    root = get_settings().media_root_path / project_id
    root.mkdir(parents=True, exist_ok=True)
    return root / "profile_candidates_staging.json"


def _candidate_to_dict(c: ProfileCandidate) -> dict[str, Any]:
    return {
        "start_seconds": c.start_seconds,
        "end_seconds": c.end_seconds,
        "text": c.text,
        "candidate_sources": c.candidate_sources,
        "raw_scores": c.raw_scores,
        "audio_peak_at": c.audio_peak_at,
        "chat_peak_at": c.chat_peak_at,
        "confidence": c.confidence,
    }


def _candidate_from_dict(d: dict[str, Any]) -> ProfileCandidate:
    return ProfileCandidate(
        start_seconds=float(d["start_seconds"]),
        end_seconds=float(d["end_seconds"]),
        text=str(d.get("text") or ""),
        candidate_sources=list(d.get("candidate_sources") or []),
        raw_scores=dict(d.get("raw_scores") or {}),
        audio_peak_at=d.get("audio_peak_at"),
        chat_peak_at=d.get("chat_peak_at"),
        confidence=float(d.get("confidence") or 0.0),
    )


def load_staged_candidates(project_id: str) -> list[ProfileCandidate]:
    path = _staging_path(project_id)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [_candidate_from_dict(c) for c in data.get("candidates", [])]


def save_staged_candidates(
    project_id: str,
    candidates: list[ProfileCandidate],
    *,
    profile_version_id: str,
) -> None:
    path = _staging_path(project_id)
    path.write_text(
        json.dumps(
            {
                "profile_version_id": profile_version_id,
                "candidates": [_candidate_to_dict(c) for c in candidates],
            },
            indent=0,
        ),
        encoding="utf-8",
    )


@register("candidate_generate")
async def handle_candidate_generate(job, progress: ProgressReporter) -> dict[str, Any]:
    payload = job.payload
    project_id = payload.get("project_id") or job.project_id
    if not project_id:
        raise ValueError("candidate_generate requires project_id")

    pipeline_run_id = ensure_run_id(project_id, payload)
    ctx = load_project_context(project_id)

    profile_id = ctx.settings.get("highlightProfileId")
    active = load_active_profile_version(
        str(profile_id) if profile_id else None
    )

    progress(0.2, "loading signal data")
    audio_series = compute_audio_features(ctx.audio_path)
    chat_density = compute_chat_density(
        ctx.chat_events,
        duration_seconds=ctx.duration_seconds or audio_series.duration_seconds,
    )

    top_n = int(ctx.settings.get("topN", 3))
    progress(0.5, "generating profile candidates")
    t0 = time.perf_counter()
    candidates = generate_profile_candidates(
        ctx.segments,
        audio=audio_series,
        chat=chat_density,
        scene_cuts=ctx.scene_cuts,
        config=active.config,
        duration_seconds=ctx.duration_seconds or audio_series.duration_seconds,
        target_count=top_n,
    )
    gen_ms = int((time.perf_counter() - t0) * 1000)

    save_staged_candidates(
        project_id,
        candidates,
        profile_version_id=active.version_id,
    )

    record_stage(
        run_id=pipeline_run_id,
        project_id=project_id,
        stage="candidate_generate",
        duration_ms=gen_ms,
        started_at=_now_ms() - gen_ms,
        finished_at=_now_ms(),
        status="ok",
        job_id=job.id,
        meta={"candidate_count": len(candidates)},
    )

    merge_flow_report(
        project_id,
        stage="candidate_generate",
        data={
            "status": "ok",
            "candidateCount": len(candidates),
            "profileVersionId": active.version_id,
            "profileSlug": active.profile_slug,
        },
        pipeline_run_id=pipeline_run_id,
    )

    from . import queue

    queue.enqueue(
        "profile_score",
        forward_payload(
            payload,
            project_id=project_id,
            video_id=ctx.video_id,
            profile_version_id=active.version_id,
        ),
        project_id=project_id,
    )

    progress(1.0, f"generated {len(candidates)} candidates")
    return {
        "project_id": project_id,
        "candidate_count": len(candidates),
        "profile_version_id": active.version_id,
        "pipeline_run_id": pipeline_run_id,
    }
