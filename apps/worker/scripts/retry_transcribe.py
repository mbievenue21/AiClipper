"""Retry the transcribe stage for a project that failed mid-pipeline.

Use case: ingest succeeded, transcribe failed (e.g. missing dependency, API
outage). Instead of deleting + recreating the project, this resets the row
back to "ingesting" and enqueues a fresh transcribe job that picks up the
existing audio.wav.

Usage:
    .venv\\Scripts\\python scripts\\retry_transcribe.py <project_id>
    pnpm --filter worker run retry:transcribe <project_id>
"""

from __future__ import annotations

import sys
import time

from sqlalchemy import select

from worker.db import session_scope
from worker.jobs import queue
from worker.models import Project, Video


def _now_ms() -> int:
    return int(time.time() * 1000)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python scripts/retry_transcribe.py <project_id>", file=sys.stderr)
        return 2

    project_id = argv[1].strip()

    with session_scope() as session:
        project = session.get(Project, project_id)
        if project is None:
            print(f"[retry] project {project_id!r} not found", file=sys.stderr)
            return 1

        video = session.execute(
            select(Video).where(Video.project_id == project_id)
        ).scalar_one_or_none()
        if video is None or not video.audio_path:
            print(
                f"[retry] project {project_id!r} has no ingested audio yet — "
                "create a new project instead.",
                file=sys.stderr,
            )
            return 1

        prev_status = project.status
        project.status = "transcribing"
        project.notes = None
        project.updated_at = _now_ms()
        video_id = video.id

    job = queue.enqueue(
        "transcribe",
        {"project_id": project_id, "video_id": video_id},
        project_id=project_id,
    )

    print(
        f"[retry] project={project_id} prev_status={prev_status} "
        f"video={video_id} enqueued_job={job.id}"
    )
    print("[retry] watch progress in the web UI — the SSE stream will pick it up.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
