"""Shared helpers for enqueueing post-transcribe pipeline jobs."""

from __future__ import annotations

from typing import Any

from ..config import get_settings
from ..providers.twelvelabs_client import TwelveLabsClient
from . import queue


def enqueue_post_transcribe(
    project_id: str,
    video_id: str,
    *,
    extra_payload: dict[str, Any] | None = None,
):
    """Chain TwelveLabs jobs when enabled, otherwise go straight to analyze."""
    payload = {"project_id": project_id, "video_id": video_id}
    if extra_payload:
        payload.update(extra_payload)

    client = TwelveLabsClient()
    if client.enabled():
        return queue.enqueue("twelvelabs_index", payload, project_id=project_id)
    return queue.enqueue("analyze", payload, project_id=project_id)


def enqueue_reanalysis(
    project_id: str,
    video_id: str,
    *,
    mode: str = "full",
    analyze_model_override: str | None = None,
    vibe_override: str | None = None,
):
    """Re-run analysis with optional TwelveLabs visual pass."""
    payload: dict[str, Any] = {
        "project_id": project_id,
        "video_id": video_id,
        "reanalysis_mode": mode,
    }
    if analyze_model_override:
        payload["analyze_model_override"] = analyze_model_override
    if vibe_override is not None:
        payload["vibe_override"] = vibe_override

    client = TwelveLabsClient()
    if mode in ("visual_only", "full") and client.enabled():
        return queue.enqueue("twelvelabs_analyze", payload, project_id=project_id)
    return queue.enqueue("analyze", payload, project_id=project_id)
