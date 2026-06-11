"""Shared helpers for enqueueing post-transcribe pipeline jobs."""

from __future__ import annotations

from typing import Any

from ..config import get_settings
from ..providers.twelvelabs_client import TwelveLabsClient
from . import queue


def forward_payload(
    job_payload: dict[str, Any],
    **fields: Any,
) -> dict[str, Any]:
    """Copy job fields into a new enqueue payload, preserving pipeline_run_id."""
    out: dict[str, Any] = dict(fields)
    run_id = job_payload.get("pipeline_run_id")
    if run_id:
        out["pipeline_run_id"] = run_id
    return out


def enqueue_post_transcribe(
    project_id: str,
    video_id: str,
    *,
    extra_payload: dict[str, Any] | None = None,
    pipeline_run_id: str | None = None,
):
    """Chain TwelveLabs when explicitly enabled; default is local profile pipeline."""
    payload: dict[str, Any] = {"project_id": project_id, "video_id": video_id}
    if pipeline_run_id:
        payload["pipeline_run_id"] = pipeline_run_id
    if extra_payload:
        payload.update(extra_payload)

    client = TwelveLabsClient()
    if client.enabled():
        return queue.enqueue("twelvelabs_index", payload, project_id=project_id)
    return queue.enqueue("feature_extract", payload, project_id=project_id)


def enqueue_reanalysis(
    project_id: str,
    video_id: str,
    *,
    mode: str = "full",
    analyze_model_override: str | None = None,
    vibe_override: str | None = None,
    pipeline_run_id: str | None = None,
):
    """Re-run analysis with optional TwelveLabs visual pass."""
    payload: dict[str, Any] = {
        "project_id": project_id,
        "video_id": video_id,
        "reanalysis_mode": mode,
    }
    if pipeline_run_id:
        payload["pipeline_run_id"] = pipeline_run_id
    if analyze_model_override:
        payload["analyze_model_override"] = analyze_model_override
    if vibe_override is not None:
        payload["vibe_override"] = vibe_override

    client = TwelveLabsClient()
    if mode in ("visual_only", "full") and client.enabled():
        return queue.enqueue("twelvelabs_analyze", payload, project_id=project_id)
    if mode == "local_only":
        return queue.enqueue("feature_extract", payload, project_id=project_id)
    return queue.enqueue("feature_extract", payload, project_id=project_id)
