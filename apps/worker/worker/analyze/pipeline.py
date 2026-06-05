"""End-to-end highlight analysis orchestrator.

Inputs are gathered by the analyze job handler; this module just does the
maths and the LLM call. It is intentionally pure of database concerns so
it can be unit-tested with synthetic transcripts.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from .audio_features import AudioFeatureSeries, compute_audio_features
from .candidates import Candidate, Segment, generate_candidates
from .chat_features import ChatDensitySeries, ChatEventOut, compute_chat_density
from .gemini import LlmPick, is_configured as gemini_configured, rerank_with_gemini

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
    # New (step 7 v2): buildup-awareness controls. Defaults match
    # DEFAULT_PROJECT_SETTINGS on the web side.
    pre_roll_seconds: float = 8.0
    tail_padding_seconds: float = 2.0
    analyze_model: str = "gemini-2.5-pro"


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

    def to_reason_json(self) -> dict[str, Any]:
        return {
            "chatScore": self.chat_score,
            "audioScore": self.audio_score,
            "llmScore": self.llm_score,
            "llmExplanation": self.llm_explanation,
            "signals": self.reason_tags,
        }


@dataclass
class AnalysisOutput:
    audio_series: AudioFeatureSeries
    chat_density: ChatDensitySeries
    candidates: list[Candidate]
    highlights: list[HighlightOut]
    used_llm: bool
    notes: list[str] = field(default_factory=list)


def analyze_project(inputs: AnalysisInput, *, progress: ProgressCb) -> AnalysisOutput:
    notes: list[str] = []

    # Phase 1: audio features.
    progress(0.05, "computing audio features (librosa)")
    audio_series = compute_audio_features(inputs.audio_path)

    # Phase 2: chat density (cheap, skip if no chat).
    progress(0.50, "computing chat density")
    chat_density = compute_chat_density(
        inputs.chat_events, duration_seconds=inputs.duration_seconds or audio_series.duration_seconds
    )
    if not inputs.chat_events:
        notes.append("No chat track available — chat score will be zero.")

    # Phase 3: candidate generation.
    progress(0.65, "generating candidate windows")
    candidates = generate_candidates(
        inputs.segments,
        audio=audio_series,
        chat=chat_density,
        min_seconds=inputs.min_clip_seconds,
        max_seconds=inputs.max_clip_seconds,
        target_count=inputs.top_n,
    )
    log.info(
        "candidates_generated",
        count=len(candidates),
        top_local=[round(c.composite_score, 3) for c in candidates[:5]],
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
            notes=notes,
        )

    # Phase 4: Gemini rerank (optional).
    progress(0.80, f"asking {inputs.analyze_model} to pick the best clips")
    llm_picks: list[LlmPick] | None = None
    if gemini_configured():
        llm_picks = rerank_with_gemini(
            candidates,
            top_n=inputs.top_n,
            vibe=inputs.vibe,
            language=inputs.language,
            model=inputs.analyze_model,
            # Cap the buildup the LLM can request to match the user's setting
            # plus a small margin (so the model has a bit of slack to pick a
            # natural boundary).
            max_pre_roll=max(inputs.pre_roll_seconds, 4.0) + 4.0,
            max_clip_seconds=inputs.max_clip_seconds,
        )
        if llm_picks is None:
            notes.append("Gemini call failed; falling back to local score.")
    else:
        notes.append("GEMINI_API_KEY not set; using local score only.")

    used_llm = bool(llm_picks)
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

    progress(0.95, "finalising highlights")
    return AnalysisOutput(
        audio_series=audio_series,
        chat_density=chat_density,
        candidates=candidates,
        highlights=highlights,
        used_llm=used_llm,
        notes=notes,
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
    """Assemble final HighlightOut rows.

    Boundary order of operations:
      1. Start from the candidate's `start_seconds` / `end_seconds`.
      2. If the LLM proposed adjusted boundaries, use those.
      3. If the LLM did NOT adjust, apply the user's pre-roll + tail padding.
      4. Snap start backwards to the nearest sentence beginning within ±2s
         (so we never start mid-word).
      5. Clamp to [0, duration] and enforce `max_clip_seconds`.
    """
    out: list[HighlightOut] = []

    if llm_picks:
        # LLM picked these candidates explicitly — respect its ranking.
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

            # Final composite: 55% LLM, 45% local. LLM judges narrative quality
            # which is what most viewers actually care about.
            composite = 0.55 * pick.llm_score + 0.45 * c.composite_score
            explanation = pick.summary or ""
            if pick.boundary_reason:
                explanation = (explanation + "\n\n" + pick.boundary_reason).strip()
            if snap_note:
                explanation = (explanation + "\n\n" + snap_note).strip()

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
                    reason_tags=pick.reason_tags
                    or _derive_signals(c, llm_used=True),
                    text_excerpt=_excerpt(c.text),
                )
            )
        return out

    # No LLM — take top-N by local score, apply padding + snapping, synthesise titles.
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
                score=c.composite_score,
                title=_fallback_title(c),
                summary=None,
                audio_score=c.audio_score,
                chat_score=c.chat_score,
                keyword_score=c.keyword_score,
                llm_score=0.0,
                llm_explanation="",
                reason_tags=_derive_signals(c, llm_used=False),
                text_excerpt=_excerpt(c.text),
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
    """See `_build_highlights` for the order of operations."""
    note_parts: list[str] = []

    # 1. + 2. Decide start/end
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

    # 3. Sentence-start snap: never start mid-word. Look ±2s for a segment
    # whose start is close to our chosen start_t. Prefer earlier (we lean
    # into more buildup, not less).
    snapped_start = _snap_to_sentence_start(start_t, segments, window=2.0)
    if snapped_start is not None and abs(snapped_start - start_t) > 0.05:
        note_parts.append(
            f"Snapped start to sentence beginning ({snapped_start:.1f}s)"
        )
        start_t = snapped_start

    # 4. Clamp
    start_t = max(0.0, start_t)
    end_t = min(duration_seconds, end_t) if duration_seconds > 0 else end_t
    if end_t - start_t > max_clip_seconds:
        # Prefer keeping climax (original end) — trim pre-roll if we're over.
        start_t = max(start_t, end_t - max_clip_seconds)
        note_parts.append(f"Capped to {max_clip_seconds:.0f}s")
    if end_t <= start_t + 1.0:
        # Degenerate guard.
        end_t = start_t + 1.0

    return start_t, end_t, "; ".join(note_parts)


def _snap_to_sentence_start(
    target_seconds: float, segments: list[Segment], *, window: float
) -> float | None:
    """Snap `target_seconds` to the nearest transcript segment start.

    Returns None if no sentence boundary is within `window`. Prefers a
    sentence start that is at or before the target (earlier = more buildup,
    safer than cutting in mid-word later).
    """
    if not segments:
        return None
    best: float | None = None
    best_dist = window + 1.0
    for seg in segments:
        dist = abs(seg.start_seconds - target_seconds)
        if dist > window:
            continue
        # Bias toward earlier starts by giving a small distance discount.
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
    if llm_used:
        tags.append("llm_pick")
    if not tags:
        tags.append("local_only")
    return tags
