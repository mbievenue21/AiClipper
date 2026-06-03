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


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)  # youtube|twitch|upload
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    notes: Mapped[str | None] = mapped_column(Text)
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
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=_now_ms)

    video: Mapped[Video] = relationship(back_populates="highlights")
    clips: Mapped[list["Clip"]] = relationship(
        back_populates="highlight", cascade="all, delete-orphan"
    )


class Clip(Base):
    __tablename__ = "clips"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    highlight_id: Mapped[str] = mapped_column(
        Text, ForeignKey("highlights.id", ondelete="CASCADE"), nullable=False
    )
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    thumbnail_path: Mapped[str | None] = mapped_column(Text)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    aspect: Mapped[str] = mapped_column(Text, nullable=False)  # 16:9 | 9:16 | 1:1
    has_captions: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=_now_ms)

    highlight: Mapped[Highlight] = relationship(back_populates="clips")
    uploads: Mapped[list["ScheduledUpload"]] = relationship(
        back_populates="clip", cascade="all, delete-orphan"
    )


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    platform: Mapped[str] = mapped_column(Text, nullable=False)  # youtube|tiktok|instagram
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
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    tags_json: Mapped[str | None] = mapped_column(Text)
    scheduled_for: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    external_id: Mapped[str | None] = mapped_column(Text)
    external_url: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=_now_ms)
    updated_at: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=_now_ms, onupdate=_now_ms
    )

    clip: Mapped[Clip] = relationship(back_populates="uploads")


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
