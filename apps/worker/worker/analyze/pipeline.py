"""End-to-end highlight analysis orchestrator."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from .audio_features import AudioFeatureSeries, compute_audio_features
from .candidates import Candidate, Segment, generate_candidates
from .chat_features import ChatDensitySeries, ChatEventOut, compute_chat_density
from .enrichment import is_enrichment_configured, run_enrichment
from .candidate_fusion import fuse_highlight_candidates
from .gemini import LlmPick, is_configured as gemini_configured, rerank_with_gemini
from .gemini_multimodal import is_multimodal_enabled, refine_boundaries_multimodal
from ..providers.twelvelabs_types import VisualSegmentResult

log = structlog.get_logger(__name__)

ProgressCb = Callable[[float, str | None], None]


@dataclass
class AnalysisInput:
    audio_path: Path
    duration_seconds: float
    segments: list[Segment]
    chat_events: list[ChatEventOut]
    language: str | None
    top_n: int
    min_clip_seconds: float
    max_clip_seconds: float
    vibe: str
    pre_roll_seconds: float = 8.0
    tail_padding_seconds: float = 2.0
    analyze_model: str = "flash"
    source_video_path: Path | None = None
    scene_cuts: list[float] | None = None
    visual_segments: list[VisualSegmentResult] | None = None
    twelvelabs_used: bool = False


@dataclass
class HighlightOut:
    start_seconds: float
    end_seconds: float
    score: float
    title: str
    summary: str | None
    audio_score: float
    chat_score: float
    keyword_score: float
    llm_score: float
    llm_explanation: str
    reason_tags: list[str]
    text_excerpt: str
    moment_type: str = ""
    confidence: float = 0.0

    visual_score: float = 0.0
    fusion_score: float = 0.0
    seed_source: str = ""
    reason_detail: dict[str, Any] = field(default_factory=dict)

    def to_reason_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "chatScore": self.chat_score,
            "audioScore": self.audio_score,
            "llmScore": self.llm_score,
            "llmExplanation": self.llm_explanation,
            "signals": self.reason_tags,
            "momentType": self.moment_type or None,
            "confidence": self.confidence if self.confidence > 0 else None,
        }
        if self.visual_score > 0:
            out["visualScore"] = self.visual_score
        if self.fusion_score > 0:
            out["fusionScore"] = self.fusion_score
        if self.seed_source:
            out["seedSource"] = self.seed_source
        if self.reason_detail:
            out.update(
                {
                    k: v
                    for k, v in self.reason_detail.items()
                    if k
                    in (
                        "scores",
                        "twelvelabs",
                        "penalties",
                        "boundaryDecision",
                        "sources",
                    )
                }
            )
        return out


@dataclass
class AnalysisOutput:
    audio_series: AudioFeatureSeries
    chat_density: ChatDensitySeries
    candidates: list[Candidate]
    highlights: list[HighlightOut]
    used_llm: bool
    scene_cuts: list[float] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    stage_timings_ms: dict[str, int] = field(default_factory=dict)


def _tick() -> float:
    return time.perf_counter()


def analyze_project(inputs: AnalysisInput, *, progress: ProgressCb) -> AnalysisOutput:
    notes: list[str] = []
    scene_cuts = list(inputs.scene_cuts or [])
    timings: dict[str, int] = {}

    progress(0.05, "computing audio features (librosa)")
    t0 = _tick()
    audio_series = compute_audio_features(inputs.audio_path)
    timings["librosa_audio"] = int((_tick() - t0) * 1000)

    if is_enrichment_configured():
        progress(0.15, "running audio enrichment pass")
        enrichment = run_enrichment(inputs.audio_path)
        if enrichment and enrichment.events:
            notes.append(
                f"Enrichment ({enrichment.backend}): {len(enrichment.events)} events."
            )

    progress(0.50, "computing chat density")
    t0 = _tick()
    chat_density = compute_chat_density(
        inputs.chat_events, duration_seconds=inputs.duration_seconds or audio_series.duration_seconds
    )
    timings["chat_density"] = int((_tick() - t0) * 1000)
    if not inputs.chat_events:
        notes.append("No chat track available — chat score will be zero.")

    progress(0.65, "generating candidate windows")
    t0 = _tick()
    local_candidates = generate_candidates(
        inputs.segments,
        audio=audio_series,
        chat=chat_density,
        min_seconds=inputs.min_clip_seconds,
        max_seconds=inputs.max_clip_seconds,
        target_count=inputs.top_n,
    )
    timings["candidate_generation"] = int((_tick() - t0) * 1000)

    visual_segments = list(inputs.visual_segments or [])
    t0 = _tick()
    if visual_segments:
        progress(0.72, "fusing local + TwelveLabs visual candidates")
        fused = fuse_highlight_candidates(
            local_candidates,
            visual_segments,
            audio=audio_series,
            chat=chat_density,
            scene_cuts=scene_cuts,
            min_clip_seconds=inputs.min_clip_seconds,
            max_clip_seconds=inputs.max_clip_seconds,
        )
        candidates = [f.to_candidate() for f in fused]
        notes.append(
            f"TwelveLabs fusion: {len(visual_segments)} visual segments, "
            f"{len(candidates)} fused candidates."
        )
    else:
        candidates = local_candidates
        if inputs.twelvelabs_used:
            notes.append("TwelveLabs enabled but no visual segments available — local only.")
    timings["candidate_fusion"] = int((_tick() - t0) * 1000)

    log.info(
        "candidates_generated",
        count=len(candidates),
        top_local=[round(c.composite_score, 3) for c in candidates[:5]],
        seeds={
            src: sum(1 for x in candidates if x.seed_source == src)
            for src in ("transcript", "audio_peak", "chat_peak")
        },
        twelvelabs_segments=len(visual_segments),
    )

    if not candidates:
        notes.append(
            "No candidates met the clip-length floor. The video may be too "
            "short or the transcript too sparse."
        )
        return AnalysisOutput(
            audio_series=audio_series,
            chat_density=chat_density,
            candidates=[],
            highlights=[],
            used_llm=False,
            scene_cuts=scene_cuts,
            notes=notes,
            stage_timings_ms=timings,
        )

    progress(0.80, f"asking {inputs.analyze_model} to pick the best clips")
    llm_picks: list[LlmPick] | None = None
    max_pre_roll = max(inputs.pre_roll_seconds, 4.0) + 4.0

    t0 = _tick()
    if gemini_configured():
        llm_picks = rerank_with_gemini(
            candidates,
            top_n=inputs.top_n,
            vibe=inputs.vibe,
            language=inputs.language,
            model=inputs.analyze_model,
            max_pre_roll=max_pre_roll,
            max_clip_seconds=inputs.max_clip_seconds,
            chat_events=inputs.chat_events,
            chat_density=chat_density,
            scene_cuts=scene_cuts,
            audio_samples=audio_series.samples,
        )
        if llm_picks is None:
            notes.append("Gemini call failed; falling back to local score.")
        elif is_multimodal_enabled() and inputs.source_video_path:
            progress(0.88, "multimodal boundary refinement")
            llm_picks = refine_boundaries_multimodal(
                candidates,
                llm_picks,
                source_video_path=inputs.source_video_path,
                max_pre_roll=max_pre_roll,
                max_clip_seconds=inputs.max_clip_seconds,
            )
            notes.append("Multimodal boundary refinement applied.")
    else:
        notes.append("GEMINI_API_KEY not set; using local score only.")
    timings["gemini_rerank"] = int((_tick() - t0) * 1000)

    used_llm = bool(llm_picks)
    t0 = _tick()
    highlights = _build_highlights(
        candidates,
        llm_picks=llm_picks,
        top_n=inputs.top_n,
        segments=inputs.segments,
        duration_seconds=inputs.duration_seconds or audio_series.duration_seconds,
        pre_roll_seconds=inputs.pre_roll_seconds,
        tail_padding_seconds=inputs.tail_padding_seconds,
        max_clip_seconds=inputs.max_clip_seconds,
    )
    timings["highlights_build"] = int((_tick() - t0) * 1000)

    progress(0.95, "finalising highlights")
    return AnalysisOutput(
        audio_series=audio_series,
        chat_density=chat_density,
        candidates=candidates,
        highlights=highlights,
        used_llm=used_llm,
        scene_cuts=scene_cuts,
        notes=notes,
        stage_timings_ms=timings,
    )


def _build_highlights(
    candidates: list[Candidate],
    *,
    llm_picks: list[LlmPick] | None,
    top_n: int,
    segments: list[Segment],
    duration_seconds: float,
    pre_roll_seconds: float,
    tail_padding_seconds: float,
    max_clip_seconds: float,
) -> list[HighlightOut]:
    out: list[HighlightOut] = []

    if llm_picks:
        for pick in llm_picks[:top_n]:
            c = candidates[pick.candidate_index]

            llm_adjusted_start = (
                pick.adjusted_start_seconds
                if pick.adjusted_start_seconds is not None
                else None
            )
            llm_adjusted_end = (
                pick.adjusted_end_seconds
                if pick.adjusted_end_seconds is not None
                else None
            )

            start_t, end_t, snap_note = _finalize_boundaries(
                base_start=c.start_seconds,
                base_end=c.end_seconds,
                llm_start=llm_adjusted_start,
                llm_end=llm_adjusted_end,
                pre_roll_seconds=pre_roll_seconds,
                tail_padding_seconds=tail_padding_seconds,
                max_clip_seconds=max_clip_seconds,
                duration_seconds=duration_seconds,
                segments=segments,
            )

            composite = 0.55 * pick.llm_score + 0.45 * c.composite_score
            explanation = pick.summary or ""
            if pick.boundary_reason:
                explanation = (explanation + "\n\n" + pick.boundary_reason).strip()
            if snap_note:
                explanation = (explanation + "\n\n" + snap_note).strip()

            tags = list(pick.reason_tags) or _derive_signals(c, llm_used=True)
            if pick.moment_type:
                tags.append(pick.moment_type)

            out.append(
                HighlightOut(
                    start_seconds=start_t,
                    end_seconds=end_t,
                    score=float(min(1.0, composite)),
                    title=pick.title or _fallback_title(c),
                    summary=pick.summary or None,
                    audio_score=c.audio_score,
                    chat_score=c.chat_score,
                    keyword_score=c.keyword_score,
                    llm_score=pick.llm_score,
                    llm_explanation=explanation,
                    reason_tags=tags,
                    text_excerpt=_excerpt(c.text),
                    moment_type=pick.moment_type or getattr(c, "moment_type", ""),
                    confidence=pick.confidence or getattr(c, "confidence", 0.0),
                    visual_score=getattr(c, "visual_score", 0.0),
                    fusion_score=getattr(c, "fusion_score", 0.0) or getattr(c, "composite_score", 0.0),
                    seed_source=c.seed_source,
                    reason_detail=getattr(c, "reason_json", {}) or {},
                )
            )
        return out

    for c in candidates[:top_n]:
        start_t, end_t, _ = _finalize_boundaries(
            base_start=c.start_seconds,
            base_end=c.end_seconds,
            llm_start=None,
            llm_end=None,
            pre_roll_seconds=pre_roll_seconds,
            tail_padding_seconds=tail_padding_seconds,
            max_clip_seconds=max_clip_seconds,
            duration_seconds=duration_seconds,
            segments=segments,
        )
        out.append(
            HighlightOut(
                start_seconds=start_t,
                end_seconds=end_t,
                score=getattr(c, "fusion_score", 0.0) or c.composite_score,
                title=_fallback_title(c),
                summary=None,
                audio_score=c.audio_score,
                chat_score=c.chat_score,
                keyword_score=c.keyword_score,
                llm_score=0.0,
                llm_explanation="",
                reason_tags=_derive_signals(c, llm_used=False),
                text_excerpt=_excerpt(c.text),
                visual_score=getattr(c, "visual_score", 0.0),
                fusion_score=getattr(c, "fusion_score", 0.0),
                seed_source=c.seed_source,
                reason_detail=getattr(c, "reason_json", {}) or {},
            )
        )
    return out


def _finalize_boundaries(
    *,
    base_start: float,
    base_end: float,
    llm_start: float | None,
    llm_end: float | None,
    pre_roll_seconds: float,
    tail_padding_seconds: float,
    max_clip_seconds: float,
    duration_seconds: float,
    segments: list[Segment],
) -> tuple[float, float, str]:
    note_parts: list[str] = []

    if llm_start is not None:
        start_t = llm_start
        note_parts.append(f"LLM extended start to {start_t:.1f}s")
    else:
        start_t = max(0.0, base_start - max(0.0, pre_roll_seconds))
        if pre_roll_seconds > 0:
            note_parts.append(f"Added {pre_roll_seconds:.0f}s pre-roll buildup")

    if llm_end is not None:
        end_t = llm_end
        note_parts.append(f"LLM extended end to {end_t:.1f}s")
    else:
        end_t = base_end + max(0.0, tail_padding_seconds)
        if tail_padding_seconds > 0:
            note_parts.append(f"Added {tail_padding_seconds:.0f}s tail reaction")

    snapped_start = _snap_to_sentence_start(start_t, segments, window=2.0)
    if snapped_start is not None and abs(snapped_start - start_t) > 0.05:
        note_parts.append(
            f"Snapped start to sentence beginning ({snapped_start:.1f}s)"
        )
        start_t = snapped_start

    start_t = max(0.0, start_t)
    end_t = min(duration_seconds, end_t) if duration_seconds > 0 else end_t
    if end_t - start_t > max_clip_seconds:
        start_t = max(start_t, end_t - max_clip_seconds)
        note_parts.append(f"Capped to {max_clip_seconds:.0f}s")
    if end_t <= start_t + 1.0:
        end_t = start_t + 1.0

    return start_t, end_t, "; ".join(note_parts)


def _snap_to_sentence_start(
    target_seconds: float, segments: list[Segment], *, window: float
) -> float | None:
    if not segments:
        return None
    best: float | None = None
    best_dist = window + 1.0
    for seg in segments:
        dist = abs(seg.start_seconds - target_seconds)
        if dist > window:
            continue
        adjusted = dist if seg.start_seconds <= target_seconds else dist + 0.5
        if adjusted < best_dist:
            best = seg.start_seconds
            best_dist = adjusted
    return best


def _fallback_title(c: Candidate) -> str:
    excerpt = c.text.strip().split(".")[0].strip()
    if not excerpt:
        return f"Highlight @ {int(c.start_seconds)}s"
    if len(excerpt) > 80:
        excerpt = excerpt[:77] + "…"
    return excerpt


def _excerpt(text: str, *, limit: int = 320) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _derive_signals(c: Candidate, *, llm_used: bool) -> list[str]:
    tags: list[str] = []
    if c.audio_score >= 0.6:
        tags.append("high_audio_energy")
    if c.chat_score >= 0.5:
        tags.append("chat_spike")
    if c.keyword_score >= 0.34:
        tags.append("salient_phrase")
    if c.seed_source == "audio_peak":
        tags.append("audio_peak_seed")
    if c.seed_source == "chat_peak":
        tags.append("chat_peak_seed")
    if llm_used:
        tags.append("llm_pick")
    if not tags:
        tags.append("local_only")
    return tags
