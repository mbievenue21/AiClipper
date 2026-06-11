"""Shared helpers to load project analysis context."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from ..analyze.candidates import Segment
from ..analyze.chat_features import ChatEventOut, iter_existing_events, parse_twitch_chat
from ..config import get_settings
from ..db import session_scope
from ..models import ChatEvent, Project, Transcript, TranscriptSegment, Video


@dataclass
class ProjectAnalysisContext:
    project_id: str
    video_id: str
    audio_path: Path
    video_path: Path | None
    chat_json_path: Path | None
    duration_seconds: float
    language: str | None
    segments: list[Segment]
    chat_events: list[ChatEventOut]
    settings: dict
    scene_cuts: list[float]


def media_abs(rel_path: str) -> Path:
    return (get_settings().media_root_path / rel_path).resolve()


def load_project_context(project_id: str) -> ProjectAnalysisContext:
    with session_scope() as session:
        project = session.get(Project, project_id)
        if project is None:
            raise ValueError(f"Project {project_id!r} not found")

        video = session.execute(
            select(Video).where(Video.project_id == project_id)
        ).scalar_one_or_none()
        if video is None or not video.audio_path:
            raise ValueError("Project requires completed ingest + transcribe.")

        transcript = session.execute(
            select(Transcript).where(Transcript.video_id == video.id)
        ).scalar_one_or_none()
        if transcript is None:
            raise ValueError("No transcript found.")

        seg_rows = (
            session.execute(
                select(TranscriptSegment)
                .where(TranscriptSegment.transcript_id == transcript.id)
                .order_by(TranscriptSegment.start_seconds)
            )
            .scalars()
            .all()
        )

        chat_rows = (
            session.execute(
                select(ChatEvent)
                .where(ChatEvent.video_id == video.id)
                .order_by(ChatEvent.timestamp_seconds)
            )
            .scalars()
            .all()
        )

        segments = [
            Segment(
                start_seconds=float(s.start_seconds),
                end_seconds=float(s.end_seconds),
                text=s.text or "",
            )
            for s in seg_rows
        ]
        chat_events = iter_existing_events(chat_rows)
        if not chat_events and video.chat_json_path:
            chat_abs = media_abs(video.chat_json_path)
            if chat_abs.exists():
                chat_events = parse_twitch_chat(chat_abs)

        scene_cuts: list[float] = []
        if video.scene_cuts_json:
            try:
                loaded = json.loads(video.scene_cuts_json)
                if isinstance(loaded, list):
                    scene_cuts = [float(x) for x in loaded]
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

        return ProjectAnalysisContext(
            project_id=project_id,
            video_id=video.id,
            audio_path=media_abs(video.audio_path),
            video_path=media_abs(video.file_path) if video.file_path else None,
            chat_json_path=media_abs(video.chat_json_path)
            if video.chat_json_path
            else None,
            duration_seconds=float(video.duration_seconds or 0.0),
            language=transcript.language,
            segments=segments,
            chat_events=chat_events,
            settings=project.settings,
            scene_cuts=scene_cuts,
        )
