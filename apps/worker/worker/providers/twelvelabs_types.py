"""Typed results for TwelveLabs Multimodal Analysis."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExternalIndexResult:
    provider: str
    provider_index_id: str | None
    provider_video_id: str | None
    provider_task_id: str | None
    status: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExternalIndexStatus:
    status: str
    provider_video_id: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class VisualSegmentResult:
    provider: str
    model: str
    source_method: str
    start_seconds: float
    end_seconds: float
    segment_type: str
    confidence: float
    title: str | None = None
    description: str | None = None
    visual_reason: str | None = None
    audio_reason: str | None = None
    speech_reason: str | None = None
    chat_reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    suggested_clip_start_seconds: float | None = None
    suggested_clip_end_seconds: float | None = None


@dataclass
class TwelveLabsPromptContext:
    vibe: str = ""
    language: str | None = None
    transcript_summary: str | None = None
    audio_peak_times: list[float] = field(default_factory=list)
    chat_peak_times: list[float] = field(default_factory=list)
    scene_cuts: list[float] = field(default_factory=list)
    duration_seconds: float = 0.0
