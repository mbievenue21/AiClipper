"""Ingest job: download source VOD with yt-dlp, probe, extract audio track.

Subprocess strategy: we run `yt-dlp` and `ffmpeg` through sync `subprocess` in
`asyncio.to_thread` rather than `asyncio.create_subprocess_exec`. On Windows
the asyncio subprocess API requires a ProactorEventLoop, and uvicorn's reload
mode sometimes leaves us on a Selector loop where subprocess raises
NotImplementedError. The threaded path works on any loop.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

# yt-dlp leaves per-format intermediate files like "source.f251.webm" and
# "source.f399.mp4" alongside the final merged "source.mp4". Skip them when
# we look for the result.
_YTDLP_FORMAT_FILE_RE = re.compile(r"^source\.f\d+\.")
_SIDECAR_SUFFIXES = {".json", ".vtt", ".srt", ".jpg", ".png", ".part", ".ytdl", ".webp"}

import structlog
from sqlalchemy import select

from ..config import get_settings
from ..db import session_scope
from ..media.ffmpeg_util import extract_mono_wav
from ..media.paths import project_dir, rel_path
from ..media.probe import probe_video
from ..models import Project, Video
from . import queue
from .handlers import ProgressReporter, register

log = structlog.get_logger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _require_binaries() -> None:
    missing = [name for name in ("yt-dlp", "ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        raise RuntimeError(
            f"Missing required binaries: {', '.join(missing)}. "
            "Install FFmpeg and yt-dlp, then run: pnpm --filter worker run setup:ingest"
        )


def _set_project_status(
    session,
    project: Project,
    status: str,
    *,
    error_note: str | None = None,
) -> None:
    project.status = status
    project.updated_at = _now_ms()
    if error_note is not None:
        project.notes = error_note


def _run_yt_dlp_sync(url: str, output_dir: Path, on_line: Callable[[str], None]) -> None:
    """Stream yt-dlp output line-by-line; raise on non-zero exit."""
    template = str(output_dir / "source.%(ext)s")
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--merge-output-format",
        "mp4",
        "-f",
        "bv*+ba/b",
        "--embed-metadata",
        "--newline",
        "-o",
        template,
        url,
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stdout is not None
    lines: list[str] = []
    try:
        for raw in proc.stdout:
            line = raw.rstrip()
            if line:
                lines.append(line)
                on_line(line)
    finally:
        code = proc.wait()

    if code != 0:
        tail = "\n".join(lines[-15:])
        raise RuntimeError(f"yt-dlp failed (exit {code}):\n{tail}")


def _clean_project_dir(output_dir: Path) -> None:
    """Remove leftover files from a previous failed attempt.

    Safe to do here because the ingest handler has already verified there is
    no `videos` row referencing these files. Skips subdirectories so other
    pipeline stages' outputs (clips/, thumbnails/) are preserved.
    """
    if not output_dir.exists():
        return
    for entry in output_dir.iterdir():
        if entry.is_file():
            try:
                entry.unlink()
            except OSError as exc:
                log.warning("cleanup_skip", path=str(entry), reason=str(exc))


def _pick_source_file(output_dir: Path) -> Path | None:
    """Find the final merged video, ignoring yt-dlp's intermediate format files."""
    final_mp4 = output_dir / "source.mp4"
    if final_mp4.exists() and final_mp4.stat().st_size > 0:
        return final_mp4

    # Fallback: any source.* that isn't a format-id intermediate or a sidecar.
    candidates = [
        p
        for p in output_dir.glob("source.*")
        if p.is_file()
        and not _YTDLP_FORMAT_FILE_RE.match(p.name)
        and p.suffix.lower() not in _SIDECAR_SUFFIXES
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


async def _run_yt_dlp(url: str, output_dir: Path, progress: ProgressReporter) -> Path:
    """Download best video+audio merged to mp4 under output_dir/source.<ext>."""
    output_dir.mkdir(parents=True, exist_ok=True)
    _clean_project_dir(output_dir)
    progress(0.05, "starting yt-dlp download")

    def on_line(line: str) -> None:
        # yt-dlp progress lines look like: [download]  12.3% of ...
        if "%" in line and "of" in line.lower():
            progress(0.1, line[:120])

    await asyncio.to_thread(_run_yt_dlp_sync, url, output_dir, on_line)

    result = _pick_source_file(output_dir)
    if result is None:
        raise RuntimeError(f"yt-dlp finished but no source file found in {output_dir}")
    return result


def _try_download_chat_sync(url: str, output_dir: Path) -> Path | None:
    chat_template = str(output_dir / "chat")
    cmd = ["yt-dlp", "--skip-download", "--write-chat", "-o", chat_template, url]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.info("chat_download_skipped", reason=(result.stderr or "")[:200])
        return None
    for ext in (".json", ".live_chat.json", ".chat.json"):
        matches = list(output_dir.glob(f"chat*{ext}"))
        if matches:
            return matches[0]
    return None


async def _try_download_chat(url: str, output_dir: Path, source_type: str) -> Path | None:
    """Best-effort Twitch chat replay JSON. Never fails ingest."""
    if source_type != "twitch":
        return None
    return await asyncio.to_thread(_try_download_chat_sync, url, output_dir)


@register("ingest")
async def handle_ingest(job, progress: ProgressReporter) -> dict[str, Any]:
    """Download project source URL, probe metadata, write `videos` row."""
    _require_binaries()
    payload = job.payload
    project_id = payload.get("project_id") or job.project_id
    if not project_id:
        raise ValueError("ingest job requires project_id in payload or job.project_id")

    settings = get_settings()
    log.info("ingest_start", project_id=project_id)

    with session_scope() as session:
        project = session.get(Project, project_id)
        if project is None:
            raise ValueError(f"Project {project_id!r} not found")

        url = payload.get("url") or project.source_url
        if not url:
            raise ValueError("No source URL on project or job payload")

        if project.source_type == "upload":
            raise ValueError("File upload ingest is not implemented yet (Step 5: URL only)")

        existing = session.execute(
            select(Video).where(Video.project_id == project_id)
        ).scalar_one_or_none()
        if existing is not None:
            raise ValueError(f"Project {project_id} already has video {existing.id}")

        _set_project_status(session, project, "ingesting")
        source_type = project.source_type

    out_dir = project_dir(project_id)
    progress(0.02, f"downloading to {out_dir}")

    try:
        source_path = await _run_yt_dlp(url, out_dir, progress)
        progress(0.75, "probing video metadata")
        meta = await asyncio.to_thread(probe_video, source_path)

        progress(0.82, "extracting mono 16 kHz audio")
        audio_path = out_dir / "audio.wav"
        await extract_mono_wav(source_path, audio_path)

        progress(0.9, "optional chat replay")
        chat_file = await _try_download_chat(url, out_dir, source_type)

        rel_video = rel_path(source_path)
        rel_audio = rel_path(audio_path)
        rel_chat = rel_path(chat_file) if chat_file else None

        progress(0.95, "saving to database")
        with session_scope() as session:
            project = session.get(Project, project_id)
            if project is None:
                raise ValueError(f"Project {project_id!r} disappeared")

            video = Video(
                project_id=project_id,
                file_path=rel_video,
                duration_seconds=meta.duration_seconds,
                width=meta.width,
                height=meta.height,
                fps=meta.fps,
                codec=meta.codec,
                size_bytes=meta.size_bytes,
                audio_path=rel_audio,
                chat_json_path=rel_chat,
            )
            session.add(video)
            session.flush()

            _set_project_status(session, project, "pending")
            session.refresh(video)
            video_id = video.id

        # Chain Step 6: hand off to the transcribe job.
        # We enqueue here (not via depends_on) because ingest is already done;
        # the new job is immediately eligible to be claimed.
        next_job = queue.enqueue(
            "transcribe",
            {"project_id": project_id, "video_id": video_id},
            project_id=project_id,
        )

        progress(1.0, "ingest complete")
        log.info(
            "ingest_done",
            project_id=project_id,
            video_id=video_id,
            file_path=rel_video,
            media_root=str(settings.media_root_path),
            next_job_id=next_job.id,
        )
        return {
            "video_id": video_id,
            "file_path": rel_video,
            "audio_path": rel_audio,
            "chat_json_path": rel_chat,
            "duration_seconds": meta.duration_seconds,
            "next_job_id": next_job.id,
        }

    except Exception as exc:
        log.exception("ingest_failed", project_id=project_id)
        with session_scope() as session:
            project = session.get(Project, project_id)
            if project is not None:
                _set_project_status(
                    session,
                    project,
                    "failed",
                    error_note=str(exc)[:2000],
                )
        raise
