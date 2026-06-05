"""Extract time-bounded MP4 chunks for TwelveLabs upload."""

from __future__ import annotations

import subprocess
from pathlib import Path

import structlog

from ..providers.twelvelabs_upload_plan import UploadChunkPlan

log = structlog.get_logger(__name__)


def chunk_output_dir(project_media_dir: Path) -> Path:
    out_dir = project_media_dir / "twelvelabs_chunks"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def extract_upload_chunk(
    source: Path,
    plan: UploadChunkPlan,
    output: Path,
) -> Path:
    """Cut ``[start_seconds, end_seconds)`` from source into a standalone MP4."""
    if not source.exists():
        raise FileNotFoundError(f"Source video missing: {source}")

    duration = plan.duration_seconds
    if duration <= 0:
        raise ValueError(f"Invalid chunk duration for index {plan.chunk_index}")

    output.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{plan.start_seconds:.3f}",
        "-i",
        str(source),
        "-t",
        f"{duration:.3f}",
        "-c",
        "copy",
        "-avoid_negative_ts",
        "make_zero",
        "-movflags",
        "+faststart",
        str(output),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-20:])
        raise RuntimeError(
            f"ffmpeg chunk extract failed ({plan.start_seconds:.1f}s–"
            f"{plan.end_seconds:.1f}s): {tail}"
        )
    if not output.exists() or output.stat().st_size < 1000:
        raise RuntimeError(f"ffmpeg produced empty chunk file: {output}")

    log.info(
        "twelvelabs_chunk_extracted",
        start=round(plan.start_seconds, 2),
        end=round(plan.end_seconds, 2),
        size_mb=round(output.stat().st_size / (1024 * 1024), 2),
        path=str(output),
    )
    return output


def materialize_upload_chunks(
    source: Path,
    plans: list[UploadChunkPlan],
    out_dir: Path,
    *,
    max_upload_bytes: int,
) -> list[tuple[int, UploadChunkPlan, Path]]:
    """Extract plans to files; bisect any chunk that still exceeds the size cap."""
    ready: list[tuple[int, UploadChunkPlan, Path]] = []
    pending: list[UploadChunkPlan] = list(plans)
    upload_index = 0

    while pending:
        plan = pending.pop(0)
        out = out_dir / (
            f"upload_{upload_index:03d}_"
            f"{int(plan.start_seconds)}_{int(plan.end_seconds)}.mp4"
        )
        extract_upload_chunk(source, plan, out)
        size = out.stat().st_size

        if size <= max_upload_bytes:
            ready.append((upload_index, plan, out))
            upload_index += 1
            continue

        log.warning(
            "twelvelabs_chunk_oversized_bisect",
            size_mb=round(size / (1024 * 1024), 2),
            max_mb=round(max_upload_bytes / (1024 * 1024), 2),
            start=plan.start_seconds,
            end=plan.end_seconds,
        )
        mid = (plan.start_seconds + plan.end_seconds) / 2.0
        if mid - plan.start_seconds < 45.0:
            raise RuntimeError(
                f"Cannot split chunk further but file is "
                f"{size / (1024**3):.2f} GB (max "
                f"{max_upload_bytes / (1024**3):.2f} GB)"
            )
        pending.insert(0, UploadChunkPlan(0, mid, plan.end_seconds))
        pending.insert(0, UploadChunkPlan(0, plan.start_seconds, mid))
        try:
            out.unlink(missing_ok=True)
        except OSError:
            pass

    return ready
