"""SQLAlchemy mirrors of the Drizzle schema.

Drizzle (in apps/web/lib/db/schema.ts) OWNS the schema — migrations are
generated and applied from there. These Python models are read/write
mirrors that target the same SQLite file. If you change schema.ts you
MUST update these models too.

Timestamps are stored as Unix-epoch milliseconds (BIGINT) so the JS side
(Drizzle's `mode: "timestamp_ms"`) and the Python side see the same
integer values. Use `.created_at_dt` etc. to get a real datetime.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import BigInteger, Boolean, Float, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .ids import new_id


def _now_ms() -> int:
    return int(time.time() * 1000)


def _to_dt(ms: int | None) -> datetime | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


_DEFAULT_PROJECT_SETTINGS: dict[str, Any] = {
    "topN": 3,
    "minClipSeconds": 20,
    "maxClipSeconds": 60,
    "aspect": "9:16",
    "vibe": "",
}


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)  # youtube|twitch|upload
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    notes: Mapped[str | None] = mapped_column(Text)
    settings_json: Mapped[str | None] = mapped_column(Text)
    pipeline_report_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=_now_ms)
    updated_at: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=_now_ms, onupdate=_now_ms
    )

    videos: Mapped[list["Video"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )

    @property
    def created_at_dt(self) -> datetime:
        return _to_dt(self.created_at)  # type: ignore[return-value]

    @property
    def settings(self) -> dict[str, Any]:
        """Merge stored settings on top of defaults so callers always get a complete dict."""
        out = dict(_DEFAULT_PROJECT_SETTINGS)
        if self.settings_json:
            try:
                out.update(json.loads(self.settings_json))
            except json.JSONDecodeError:
                pass
        return out

    @settings.setter
    def settings(self, value: dict[str, Any]) -> None:
        self.settings_json = json.dumps(value) if value is not None else None

    @property
    def pipeline_report(self) -> dict[str, Any]:
        if self.pipeline_report_json:
            try:
                loaded = json.loads(self.pipeline_report_json)
                if isinstance(loaded, dict):
                    return loaded
            except json.JSONDecodeError:
                pass
        return {}

    @pipeline_report.setter
    def pipeline_report(self, value: dict[str, Any]) -> None:
        self.pipeline_report_json = json.dumps(value) if value else None


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        Text, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    fps: Mapped[float | None] = mapped_column(Float)
    codec: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    audio_path: Mapped[str | None] = mapped_column(Text)
    chat_json_path: Mapped[str | None] = mapped_column(Text)
    scene_cuts_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=_now_ms)

    project: Mapped[Project] = relationship(back_populates="videos")
    transcript: Mapped["Transcript | None"] = relationship(
        back_populates="video", uselist=False, cascade="all, delete-orphan"
    )
    chat_events: Mapped[list["ChatEvent"]] = relationship(
        back_populates="video", cascade="all, delete-orphan"
    )
    highlights: Mapped[list["Highlight"]] = relationship(
        back_populates="video", cascade="all, delete-orphan"
    )


class Transcript(Base):
    __tablename__ = "transcripts"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    video_id: Mapped[str] = mapped_column(
        Text, ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    language: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    full_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=_now_ms)

    video: Mapped[Video] = relationship(back_populates="transcript")
    segments: Mapped[list["TranscriptSegment"]] = relationship(
        back_populates="transcript", cascade="all, delete-orphan"
    )


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    transcript_id: Mapped[str] = mapped_column(
        Text, ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False
    )
    start_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    end_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Stored as JSON text; use .words / .words = value to (de)serialize.
    words_json: Mapped[str | None] = mapped_column(Text)

    transcript: Mapped[Transcript] = relationship(back_populates="segments")

    @property
    def words(self) -> list[dict[str, Any]]:
        return json.loads(self.words_json) if self.words_json else []

    @words.setter
    def words(self, value: list[dict[str, Any]]) -> None:
        self.words_json = json.dumps(value) if value else None


class ChatEvent(Base):
    __tablename__ = "chat_events"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    video_id: Mapped[str] = mapped_column(
        Text, ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
    )
    timestamp_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    username: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str | None] = mapped_column(Text)
    emote_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    message_type: Mapped[str | None] = mapped_column(Text)

    video: Mapped[Video] = relationship(back_populates="chat_events")


class AudioFeatures(Base):
    __tablename__ = "audio_features"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    video_id: Mapped[str] = mapped_column(
        Text, ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    samples_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=_now_ms)

    @property
    def samples(self) -> list[dict[str, float]]:
        return json.loads(self.samples_json)

    @samples.setter
    def samples(self, value: list[dict[str, float]]) -> None:
        self.samples_json = json.dumps(value)


class ChatFeatures(Base):
    __tablename__ = "chat_features"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    video_id: Mapped[str] = mapped_column(
        Text, ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    density_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=_now_ms)


class Highlight(Base):
    __tablename__ = "highlights"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    video_id: Mapped[str] = mapped_column(
        Text, ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
    )
    start_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    end_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    reason_json: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="candidate")
    generated_metadata_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=_now_ms)

    video: Mapped[Video] = relationship(back_populates="highlights")
    clips: Mapped[list["Clip"]] = relationship(
        back_populates="highlight", cascade="all, delete-orphan"
    )


_DEFAULT_CAPTION_SETTINGS: dict[str, Any] = {
    "font": "anton",
    "style": "highlight",
    "autoColor": True,
    "primaryColor": "#FFD700",
    "accentColor": "#FFFFFF",
    "uppercase": True,
}


class Clip(Base):
    __tablename__ = "clips"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    highlight_id: Mapped[str] = mapped_column(
        Text, ForeignKey("highlights.id", ondelete="CASCADE"), nullable=False
    )
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    captioned_file_path: Mapped[str | None] = mapped_column(Text)
    thumbnail_path: Mapped[str | None] = mapped_column(Text)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    width_px: Mapped[int | None] = mapped_column(Integer)
    height_px: Mapped[int | None] = mapped_column(Integer)
    aspect: Mapped[str] = mapped_column(Text, nullable=False)  # 16:9 | 9:16 | 1:1
    has_captions: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="rendering")
    dominant_color: Mapped[str | None] = mapped_column(Text)
    caption_style_json: Mapped[str | None] = mapped_column(Text)
    source_start_seconds: Mapped[float | None] = mapped_column(Float)
    source_end_seconds: Mapped[float | None] = mapped_column(Float)
    trim_start_seconds: Mapped[float | None] = mapped_column(Float)
    trim_end_seconds: Mapped[float | None] = mapped_column(Float)
    caption_segments_json: Mapped[str | None] = mapped_column(Text)
    parent_clip_id: Mapped[str | None] = mapped_column(Text)
    version_label: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    superseded_at: Mapped[int | None] = mapped_column(BigInteger)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=_now_ms)
    updated_at: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=_now_ms, onupdate=_now_ms
    )

    highlight: Mapped[Highlight] = relationship(back_populates="clips")
    uploads: Mapped[list["ScheduledUpload"]] = relationship(
        back_populates="clip", cascade="all, delete-orphan"
    )

    @property
    def caption_style(self) -> dict[str, Any]:
        out = dict(_DEFAULT_CAPTION_SETTINGS)
        if self.caption_style_json:
            try:
                out.update(json.loads(self.caption_style_json))
            except json.JSONDecodeError:
                pass
        return out

    @caption_style.setter
    def caption_style(self, value: dict[str, Any]) -> None:
        self.caption_style_json = json.dumps(value) if value is not None else None


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    platform: Mapped[str] = mapped_column(Text, nullable=False)  # youtube|instagram
    label: Mapped[str] = mapped_column(Text, nullable=False)
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[int | None] = mapped_column(BigInteger)
    raw_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=_now_ms)
    updated_at: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=_now_ms, onupdate=_now_ms
    )


class ScheduledUpload(Base):
    __tablename__ = "scheduled_uploads"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    clip_id: Mapped[str] = mapped_column(
        Text, ForeignKey("clips.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[str] = mapped_column(
        Text, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[str] = mapped_column(Text, nullable=False)  # youtube | instagram
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    tags_json: Mapped[str | None] = mapped_column(Text)
    visibility: Mapped[str] = mapped_column(Text, nullable=False, default="private")
    timezone: Mapped[str] = mapped_column(Text, nullable=False, default="America/Chicago")
    scheduled_for: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    external_id: Mapped[str | None] = mapped_column(Text)
    external_url: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=_now_ms)
    updated_at: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=_now_ms, onupdate=_now_ms
    )

    clip: Mapped[Clip] = relationship(back_populates="uploads")

    @property
    def tags(self) -> list[str]:
        return json.loads(self.tags_json) if self.tags_json else []

    @tags.setter
    def tags(self, value: list[str] | None) -> None:
        self.tags_json = json.dumps(value) if value else None


class ExternalVideoIndex(Base):
    __tablename__ = "external_video_indexes"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        Text, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    video_id: Mapped[str] = mapped_column(
        Text, ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    provider_index_id: Mapped[str | None] = mapped_column(Text)
    provider_video_id: Mapped[str | None] = mapped_column(Text)
    provider_task_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    source_path: Mapped[str | None] = mapped_column(Text)
    source_sha256: Mapped[str | None] = mapped_column(Text)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunk_start_seconds: Mapped[float | None] = mapped_column(Float, default=0.0)
    chunk_end_seconds: Mapped[float | None] = mapped_column(Float)
    metadata_json: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=_now_ms)
    updated_at: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=_now_ms, onupdate=_now_ms
    )


class VisualSegment(Base):
    __tablename__ = "visual_segments"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        Text, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    video_id: Mapped[str] = mapped_column(
        Text, ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(Text)
    source_method: Mapped[str] = mapped_column(Text, nullable=False)
    start_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    end_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    segment_type: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    visual_reason: Mapped[str | None] = mapped_column(Text)
    audio_reason: Mapped[str | None] = mapped_column(Text)
    speech_reason: Mapped[str | None] = mapped_column(Text)
    chat_reason: Mapped[str | None] = mapped_column(Text)
    raw_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=_now_ms)
    updated_at: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=_now_ms, onupdate=_now_ms
    )


class HighlightCandidate(Base):
    __tablename__ = "highlight_candidates"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        Text, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    video_id: Mapped[str] = mapped_column(
        Text, ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    start_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    end_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    seed_source: Mapped[str | None] = mapped_column(Text)
    moment_type: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    local_score: Mapped[float | None] = mapped_column(Float)
    transcript_score: Mapped[float | None] = mapped_column(Float)
    audio_score: Mapped[float | None] = mapped_column(Float)
    chat_score: Mapped[float | None] = mapped_column(Float)
    scene_score: Mapped[float | None] = mapped_column(Float)
    visual_score: Mapped[float | None] = mapped_column(Float)
    multimodal_score: Mapped[float | None] = mapped_column(Float)
    fusion_score: Mapped[float | None] = mapped_column(Float)
    audio_peak_at: Mapped[float | None] = mapped_column(Float)
    chat_peak_at: Mapped[float | None] = mapped_column(Float)
    visual_peak_at: Mapped[float | None] = mapped_column(Float)
    title: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    reason_json: Mapped[str | None] = mapped_column(Text)
    raw_provider_json: Mapped[str | None] = mapped_column(Text)
    selected_for_rerank: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    selected_as_highlight: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=_now_ms)
    updated_at: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=_now_ms, onupdate=_now_ms
    )


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    type: Mapped[str] = mapped_column(Text, nullable=False)  # ingest|transcribe|analyze|render|publish
    project_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("projects.id", ondelete="CASCADE")
    )
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    progress_message: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    depends_on_job_id: Mapped[str | None] = mapped_column(Text)
    result_json: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=_now_ms)
    started_at: Mapped[int | None] = mapped_column(BigInteger)
    finished_at: Mapped[int | None] = mapped_column(BigInteger)

    @property
    def payload(self) -> dict[str, Any]:
        return json.loads(self.payload_json)

    @payload.setter
    def payload(self, value: dict[str, Any]) -> None:
        self.payload_json = json.dumps(value)

    @property
    def result(self) -> dict[str, Any] | None:
        return json.loads(self.result_json) if self.result_json else None

    @result.setter
    def result(self, value: dict[str, Any] | None) -> None:
        self.result_json = json.dumps(value) if value is not None else None


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        Text, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="running")
    started_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=_now_ms)
    finished_at: Mapped[int | None] = mapped_column(BigInteger)
    video_duration_seconds: Mapped[float | None] = mapped_column(Float)
    twelvelabs_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_reanalysis: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    meta_json: Mapped[str | None] = mapped_column(Text)

    @property
    def meta(self) -> dict[str, Any]:
        return json.loads(self.meta_json) if self.meta_json else {}

    @meta.setter
    def meta(self, value: dict[str, Any]) -> None:
        self.meta_json = json.dumps(value) if value else None


class PipelineStageTiming(Base):
    __tablename__ = "pipeline_stage_timings"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("pipeline_runs.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[str] = mapped_column(
        Text, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[int | None] = mapped_column(BigInteger)
    finished_at: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="ok")
    job_id: Mapped[str | None] = mapped_column(Text)
    meta_json: Mapped[str | None] = mapped_column(Text)

    @property
    def meta(self) -> dict[str, Any]:
        return json.loads(self.meta_json) if self.meta_json else {}

    @meta.setter
    def meta(self, value: dict[str, Any]) -> None:
        self.meta_json = json.dumps(value) if value else None
