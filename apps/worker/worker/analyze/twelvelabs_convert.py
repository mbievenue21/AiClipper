"""Convert TwelveLabs results to visual segments and highlight candidates."""

from __future__ import annotations

from typing import Any

from ..providers.twelvelabs_types import VisualSegmentResult

_QUERY_MOMENT_MAP: dict[str, str] = {
    "surprised reaction": "surprised_reaction",
    "laughs or reacts": "funny_visual",
    "angry or frustrated": "rage_moment",
    "gameplay fail": "gameplay_fail",
    "gameplay win": "gameplay_win",
    "clutch": "gameplay_win",
    "chat reacts": "chat_hype_visual",
    "funny moment": "funny_visual",
    "jump scare": "jump_scare",
    "high energy": "high_energy_action",
    "clip that": "chat_hype_visual",
    "awkward": "awkward_or_unexpected_moment",
    "on screen event": "on_screen_event",
    "setup and payoff": "visual_payoff",
    "clutch kill": "gameplay_win",
    "dies unexpectedly": "gameplay_fail",
    "missed shot": "funny_visual",
    "team fight": "high_energy_action",
    "panic": "streamer_reaction",
}


def overlap_ratio(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    lo = max(a_start, b_start)
    hi = min(a_end, b_end)
    inter = max(0.0, hi - lo)
    union = (a_end - a_start) + (b_end - b_start) - inter
    return 0.0 if union <= 0 else inter / union


def deduplicate_visual_segments(
    segments: list[VisualSegmentResult],
    *,
    overlap_threshold: float = 0.6,
) -> list[VisualSegmentResult]:
    """Keep best segment when overlaps share type."""
    if not segments:
        return []
    sorted_segs = sorted(segments, key=lambda s: s.confidence, reverse=True)
    kept: list[VisualSegmentResult] = []
    for seg in sorted_segs:
        duplicate = False
        for k in kept:
            if seg.segment_type != k.segment_type:
                continue
            if overlap_ratio(seg.start_seconds, seg.end_seconds, k.start_seconds, k.end_seconds) >= overlap_threshold:
                duplicate = True
                break
        if not duplicate:
            kept.append(seg)
    return kept


def offset_segment_timestamps(
    segments: list[VisualSegmentResult],
    chunk_offset_seconds: float,
) -> list[VisualSegmentResult]:
    """Shift chunk-relative timestamps to full-VOD time."""
    out: list[VisualSegmentResult] = []
    for seg in segments:
        out.append(
            VisualSegmentResult(
                provider=seg.provider,
                model=seg.model,
                source_method=seg.source_method,
                start_seconds=seg.start_seconds + chunk_offset_seconds,
                end_seconds=seg.end_seconds + chunk_offset_seconds,
                segment_type=seg.segment_type,
                confidence=seg.confidence,
                title=seg.title,
                description=seg.description,
                visual_reason=seg.visual_reason,
                audio_reason=seg.audio_reason,
                speech_reason=seg.speech_reason,
                chat_reason=seg.chat_reason,
                raw=seg.raw,
                suggested_clip_start_seconds=(
                    seg.suggested_clip_start_seconds + chunk_offset_seconds
                    if seg.suggested_clip_start_seconds is not None
                    else None
                ),
                suggested_clip_end_seconds=(
                    seg.suggested_clip_end_seconds + chunk_offset_seconds
                    if seg.suggested_clip_end_seconds is not None
                    else None
                ),
            )
        )
    return out


def parse_pegasus_segments(
    payload: dict[str, Any],
    *,
    model: str,
    chunk_offset: float = 0.0,
    min_confidence: float = 0.55,
) -> list[VisualSegmentResult]:
    """Parse Pegasus JSON into VisualSegmentResult rows."""
    raw_segments = payload.get("segments") or []
    if isinstance(payload.get("data"), dict):
        raw_segments = payload["data"].get("segments") or raw_segments

    out: list[VisualSegmentResult] = []
    for item in raw_segments:
        try:
            start = float(item.get("start_seconds", item.get("start", 0)))
            end = float(item.get("end_seconds", item.get("end", 0)))
            if end <= start:
                continue
            conf = float(item.get("confidence", 0.5))
            if conf < min_confidence:
                continue
            seg_type = str(item.get("segment_type") or "visual_payoff").strip()[:64]
            if item.get("is_commentary_only"):
                seg_type = "commentary_only"
            if item.get("is_dead_air_or_menu"):
                seg_type = "dead_air_or_menu"

            sug_start = item.get("suggested_clip_start_seconds")
            sug_end = item.get("suggested_clip_end_seconds")
            out.append(
                VisualSegmentResult(
                    provider="twelvelabs",
                    model=model,
                    source_method="pegasus_segmentation",
                    start_seconds=start + chunk_offset,
                    end_seconds=end + chunk_offset,
                    segment_type=seg_type,
                    confidence=min(1.0, max(0.0, conf)),
                    title=str(item.get("title") or "")[:200] or None,
                    description=str(
                        item.get("description") or item.get("why_clip_worthy") or ""
                    )[:500]
                    or None,
                    visual_reason=str(item.get("visual_reason") or "")[:500] or None,
                    audio_reason=str(item.get("audio_reason") or "")[:500] or None,
                    speech_reason=str(item.get("speech_reason") or "")[:500] or None,
                    raw=dict(item),
                    suggested_clip_start_seconds=(
                        float(sug_start) + chunk_offset if sug_start is not None else None
                    ),
                    suggested_clip_end_seconds=(
                        float(sug_end) + chunk_offset if sug_end is not None else None
                    ),
                )
            )
        except (TypeError, ValueError):
            continue
    return out


def infer_moment_type_from_query(query: str) -> str:
    q = query.lower()
    for needle, moment in _QUERY_MOMENT_MAP.items():
        if needle in q:
            return moment
    return "visual_payoff"


def parse_marengo_search_hits(
    payload: dict[str, Any],
    *,
    query: str,
    model: str,
    duration_seconds: float,
    min_confidence: float = 0.55,
    expand_start: float = 12.0,
    expand_end: float = 15.0,
) -> list[VisualSegmentResult]:
    """Convert Marengo search hits to expanded visual segment candidates."""
    data = payload.get("data") or payload.get("clips") or payload.get("search_results") or []
    if isinstance(data, dict):
        data = data.get("data") or []

    out: list[VisualSegmentResult] = []
    moment_type = infer_moment_type_from_query(query)

    for item in data:
        try:
            start = float(
                item.get("start")
                or item.get("start_seconds")
                or (item.get("start_time") or 0)
            )
            end = float(
                item.get("end")
                or item.get("end_seconds")
                or (item.get("end_time") or start + 5)
            )
            score = float(item.get("score") or item.get("confidence") or item.get("similarity") or 0.6)
            if score < min_confidence:
                continue

            cand_start = max(0.0, start - expand_start)
            cand_end = min(duration_seconds, end + expand_end) if duration_seconds > 0 else end + expand_end
            if cand_end <= cand_start:
                continue

            out.append(
                VisualSegmentResult(
                    provider="twelvelabs",
                    model=model,
                    source_method="marengo_search",
                    start_seconds=cand_start,
                    end_seconds=cand_end,
                    segment_type=moment_type,
                    confidence=min(1.0, max(0.0, score)),
                    title=None,
                    description=f"Marengo hit for: {query[:120]}",
                    visual_reason=str(item.get("text") or item.get("transcription") or query)[:500],
                    raw={"query": query, **dict(item)},
                    suggested_clip_start_seconds=cand_start,
                    suggested_clip_end_seconds=cand_end,
                )
            )
        except (TypeError, ValueError):
            continue
    return out
