"""Plan TwelveLabs upload chunks when source files exceed API size limits."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UploadChunkPlan:
    chunk_index: int
    start_seconds: float
    end_seconds: float

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.end_seconds - self.start_seconds)


def plan_upload_chunks(
    file_size_bytes: int,
    duration_seconds: float,
    *,
    max_upload_bytes: int,
    max_chunk_seconds: float,
    overlap_seconds: float,
    size_safety_ratio: float = 0.92,
) -> list[UploadChunkPlan]:
    """Split a VOD into upload windows that stay under TwelveLabs file size caps.

    TwelveLabs ``POST /tasks`` rejects files >= 2 GB. We plan time windows using
    the observed bytes/second ratio, capped by ``max_chunk_seconds`` (default 2h).
    """
    if file_size_bytes <= 0 or duration_seconds <= 0:
        return [UploadChunkPlan(0, 0.0, max(duration_seconds, 0.0))]

    if file_size_bytes <= max_upload_bytes:
        return [UploadChunkPlan(0, 0.0, duration_seconds)]

    bytes_per_second = file_size_bytes / duration_seconds
    budget_bytes = int(max_upload_bytes * size_safety_ratio)
    seconds_per_chunk = min(
        max_chunk_seconds,
        budget_bytes / max(bytes_per_second, 1.0),
    )
    # Avoid thousands of tiny uploads on edge cases.
    seconds_per_chunk = max(120.0, seconds_per_chunk)

    chunks: list[UploadChunkPlan] = []
    start = 0.0
    idx = 0
    while start < duration_seconds - 0.5:
        end = min(duration_seconds, start + seconds_per_chunk)
        if end - start < 30.0 and chunks:
            chunks[-1] = UploadChunkPlan(
                chunks[-1].chunk_index,
                chunks[-1].start_seconds,
                duration_seconds,
            )
            break
        chunks.append(UploadChunkPlan(idx, start, end))
        if end >= duration_seconds - 0.5:
            break
        start = max(0.0, end - overlap_seconds)
        idx += 1

    return chunks or [UploadChunkPlan(0, 0.0, duration_seconds)]
