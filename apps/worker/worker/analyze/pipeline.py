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
    progress(0.80, "asking gemini to pick the best clips")
    llm_picks: list[LlmPick] | None = None
    if gemini_configured():
        llm_picks = rerank_with_gemini(
            candidates,
            top_n=inputs.top_n,
            vibe=inputs.vibe,
            language=inputs.language,
        )
        if llm_picks is None:
            notes.append("Gemini call failed; falling back to local score.")
    else:
        notes.append("GEMINI_API_KEY not set; using local score only.")

    used_llm = bool(llm_picks)
    highlights = _build_highlights(
        candidates, llm_picks=llm_picks, top_n=inputs.top_n
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
) -> list[HighlightOut]:
    out: list[HighlightOut] = []

    if llm_picks:
        # LLM picked these candidates explicitly — respect its ranking.
        for pick in llm_picks[:top_n]:
            c = candidates[pick.candidate_index]
            # Final composite: 55% LLM, 45% local. LLM judges narrative quality
            # which is what most viewers actually care about.
            composite = 0.55 * pick.llm_score + 0.45 * c.composite_score
            out.append(
                HighlightOut(
                    start_seconds=c.start_seconds,
                    end_seconds=c.end_seconds,
                    score=float(min(1.0, composite)),
                    title=pick.title or _fallback_title(c),
                    summary=pick.summary or None,
                    audio_score=c.audio_score,
                    chat_score=c.chat_score,
                    keyword_score=c.keyword_score,
                    llm_score=pick.llm_score,
                    llm_explanation=pick.summary or "",
                    reason_tags=pick.reason_tags
                    or _derive_signals(c, llm_used=True),
                    text_excerpt=_excerpt(c.text),
                )
            )
        return out

    # No LLM — take top-N by local score and synthesise titles.
    for c in candidates[:top_n]:
        out.append(
            HighlightOut(
                start_seconds=c.start_seconds,
                end_seconds=c.end_seconds,
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
