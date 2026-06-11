"""Extract features from reference clips for training."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import structlog

from ..config import get_settings
from ..db import session_scope
from ..media.ffmpeg_util import extract_mono_wav
from ..models import ExtractedFeatureWindow, ReferenceClip, TrainingExample, TrainingDataset
from ..profile.config import default_valorant_config
from ..profile.features import extract_window_features
from ..profile.loader import load_active_profile_version
from ..analyze.audio_features import compute_audio_features
from .handlers import ProgressReporter, register
from .pipeline_enqueue import forward_payload

log = structlog.get_logger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


@register("reference_feature_extract")
async def handle_reference_feature_extract(
    job, progress: ProgressReporter
) -> dict[str, Any]:
    payload = job.payload
    reference_clip_id = payload.get("reference_clip_id")
    dataset_id = payload.get("dataset_id")
    profile_id = payload.get("profile_id")

    if not reference_clip_id or not dataset_id:
        raise ValueError("reference_feature_extract requires reference_clip_id and dataset_id")

    with session_scope() as session:
        clip = session.get(ReferenceClip, reference_clip_id)
        if clip is None:
            raise ValueError(f"Reference clip {reference_clip_id} not found")
        file_path = clip.file_path
        duration = float(clip.duration_seconds or 0.0)
        dataset = session.get(TrainingDataset, dataset_id)

    media_root = get_settings().media_root_path
    normalized = str(file_path).replace("\\", "/")
    video_abs = (media_root / normalized).resolve()
    if not video_abs.exists():
        raise FileNotFoundError(
            f"Reference clip file missing at {video_abs} (stored as {file_path!r}). "
            "If training already ran cleanup, re-submit the clip import."
        )

    progress(0.2, "extracting reference audio")
    audio_path = video_abs.with_suffix(".wav")
    if not audio_path.exists():
        await extract_mono_wav(video_abs, audio_path)

    progress(0.5, "computing reference features")
    audio_series = await asyncio.to_thread(compute_audio_features, audio_path)
    active = load_active_profile_version(profile_id)

    start = 0.0
    end = duration if duration > 0 else audio_series.duration_seconds
    feats = extract_window_features(
        start_seconds=start,
        end_seconds=end,
        segments=[],
        audio=audio_series,
        chat=None,
        scene_cuts=None,
        config=active.config,
        duration_seconds=end,
        candidate_sources=["reference_clip"],
    )

    example_id: str | None = None
    with session_scope() as session:
        clip = session.get(ReferenceClip, reference_clip_id)
        if clip is None:
            raise ValueError("Reference clip disappeared")
        dataset = session.get(TrainingDataset, dataset_id)

        row = ExtractedFeatureWindow(
            reference_clip_id=reference_clip_id,
            start_seconds=start,
            end_seconds=end,
            window_size_seconds=end - start,
            transcript_text=feats.transcript_text,
        )
        row.features_json = json.dumps(feats.to_dict())
        row.audio_features_json = json.dumps(feats.audio)
        row.visual_features_json = json.dumps(feats.visual)
        session.add(row)

        label = str(payload.get("label") or "positive")
        if label not in ("positive", "negative", "accepted", "rejected", "published"):
            label = "positive"
        reason = "reference_clip" if label == "positive" else "random_negative"

        example = TrainingExample(
            dataset_id=dataset_id,
            reference_clip_id=reference_clip_id,
            start_seconds=start,
            end_seconds=end,
            label=label,
            confidence=1.0,
            reason=reason,
        )
        feat_payload: dict = feats.to_dict()
        editor_notes = payload.get("editor_notes")
        if isinstance(editor_notes, dict):
            feat_payload["editorNotes"] = editor_notes
        example.features = feat_payload
        session.add(example)
        session.flush()
        example_id = example.id

        if dataset:
            dataset.updated_at = _now_ms()

    editor_notes = payload.get("editor_notes")
    if (
        isinstance(editor_notes, dict)
        and example_id
        and editor_notes.get("enrichWithGemini") is not False
    ):
        from . import queue

        queue.enqueue(
            "enrich_training_feedback",
            {
                "profile_id": profile_id,
                "training_example_id": example_id,
            },
        )

    progress(1.0, "reference features extracted")
    return {"reference_clip_id": reference_clip_id, "features_saved": True}
