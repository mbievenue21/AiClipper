"""Per-project pipeline flow report for debugging and optimization."""

from __future__ import annotations

import time
from typing import Any

from .db import session_scope
from .models import Project

REPORT_VERSION = 1


def _now_ms() -> int:
    return int(time.time() * 1000)


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in patch.items():
        if (
            key in out
            and isinstance(out[key], dict)
            and isinstance(value, dict)
        ):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_flow_report(project_id: str) -> dict[str, Any]:
    with session_scope() as session:
        project = session.get(Project, project_id)
        if project is None:
            return {}
        return dict(project.pipeline_report or {})


def merge_flow_report(
    project_id: str,
    *,
    stage: str,
    data: dict[str, Any],
    pipeline_run_id: str | None = None,
    settings_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge one stage section into the project flow report."""
    with session_scope() as session:
        project = session.get(Project, project_id)
        if project is None:
            return {}

        current = dict(project.pipeline_report or {})
        if not current:
            current = {
                "version": REPORT_VERSION,
                "generatedAt": _now_ms(),
                "pipelineRunId": pipeline_run_id,
                "projectSettings": settings_snapshot or _settings_snapshot(project),
                "stages": {},
                "decisions": [],
            }
        elif pipeline_run_id and not current.get("pipelineRunId"):
            current["pipelineRunId"] = pipeline_run_id

        stages = dict(current.get("stages") or {})
        stage_entry = dict(stages.get(stage) or {})
        stage_entry.update(data)
        stage_entry["updatedAt"] = _now_ms()
        stages[stage] = stage_entry
        current["stages"] = stages
        current["generatedAt"] = _now_ms()
        if settings_snapshot:
            current["projectSettings"] = settings_snapshot

        project.pipeline_report = current
        return current


def finalize_flow_report(
    project_id: str,
    *,
    decisions: list[str],
    pipeline_run_id: str | None = None,
) -> dict[str, Any]:
    """Attach human-readable path summary after analyze completes."""
    with session_scope() as session:
        project = session.get(Project, project_id)
        if project is None:
            return {}

        current = dict(project.pipeline_report or {})
        current["decisions"] = decisions
        current["generatedAt"] = _now_ms()
        current["complete"] = True
        if pipeline_run_id:
            current["pipelineRunId"] = pipeline_run_id
        project.pipeline_report = current
        return current


def build_path_decisions(report: dict[str, Any]) -> list[str]:
    """Derive a readable bullet list from stage data."""
    stages = report.get("stages") or {}
    decisions: list[str] = []

    ingest = stages.get("ingest") or {}
    if ingest:
        if ingest.get("skipped"):
            decisions.append(f"Ingest: skipped ({ingest.get('reason', 'unknown')})")
        else:
            chat = "with chat" if ingest.get("chatDownloaded") else "no chat file"
            decisions.append(
                f"Ingest: {ingest.get('sourceType', 'url')} → "
                f"{ingest.get('durationSeconds', '?')}s video ({chat})"
            )

    transcribe = stages.get("transcribe") or {}
    if transcribe:
        if transcribe.get("skipped"):
            decisions.append(
                f"Transcribe: reused existing ({transcribe.get('segmentCount', 0)} segments)"
            )
        else:
            decisions.append(
                f"Transcribe: {transcribe.get('backend', 'unknown')} / "
                f"{transcribe.get('model', 'unknown')} "
                f"({transcribe.get('segmentCount', 0)} segments, "
                f"lang={transcribe.get('language', '?')})"
            )

    tl_index = stages.get("twelvelabs_index") or {}
    if tl_index:
        if tl_index.get("skipped"):
            decisions.append(
                f"TwelveLabs index: skipped — {tl_index.get('reason', 'disabled')}"
            )
        elif tl_index.get("reused"):
            decisions.append(
                f"TwelveLabs index: reused existing ({tl_index.get('chunkCount', 0)} chunks)"
            )
        else:
            decisions.append(
                f"TwelveLabs index: uploaded {tl_index.get('chunkCount', 0)} chunk(s)"
            )

    tl_analyze = stages.get("twelvelabs_analyze") or {}
    if not tl_analyze and tl_index.get("skipped"):
        decisions.append(
            f"TwelveLabs analyze: not run — index skipped ({tl_index.get('reason', 'n/a')})"
        )
    elif tl_analyze:
        if tl_analyze.get("skipped"):
            decisions.append(
                f"TwelveLabs analyze: skipped — {tl_analyze.get('reason', 'n/a')}"
            )
        elif tl_analyze.get("failedOpen"):
            decisions.append(
                "TwelveLabs analyze: failed (fail-open) — continued with 0 visual segments"
            )
        elif int(tl_analyze.get("visualSegmentCount") or 0) == 0:
            peaks = tl_analyze.get("peaksFed") or {}
            audio_n = peaks.get("audioPeakCount", 0)
            chat_n = peaks.get("chatPeakCount", 0)
            pegasus_errors = tl_analyze.get("pegasusErrors") or []
            marengo_raw = int(tl_analyze.get("marengoRawHits") or 0)
            min_conf = tl_analyze.get("minVisualConfidence", 0.55)
            err_note = ""
            if pegasus_errors:
                err_note = (
                    f" Pegasus failed: {pegasus_errors[0].get('error', 'unknown')[:80]}."
                )
            elif int(tl_analyze.get("pegasusCount") or 0) == 0:
                err_note = " Pegasus returned 0 segments."
            if marengo_raw == 0:
                marengo_note = " Marengo: 0 raw search hits."
            elif int(tl_analyze.get("marengoCount") or 0) == 0:
                marengo_note = (
                    f" Marengo: {marengo_raw} raw hits filtered to 0 "
                    f"(min confidence {min_conf})."
                )
            else:
                marengo_note = ""
            decisions.append(
                "TwelveLabs analyze: 0 visual segments — local fusion skipped."
                f"{err_note}{marengo_note} "
                f"(peaks fed: audio={audio_n} chat={chat_n})"
            )
        else:
            peaks = tl_analyze.get("peaksFed") or {}
            audio_n = peaks.get("audioPeakCount", 0)
            chat_n = peaks.get("chatPeakCount", 0)
            vibe = tl_analyze.get("vibeUsed") or ""
            vibe_note = f', vibe="{vibe[:40]}"' if vibe else ", no vibe set"
            decisions.append(
                f"TwelveLabs analyze: {tl_analyze.get('visualSegmentCount', 0)} visual segments "
                f"(Pegasus {tl_analyze.get('pegasusCount', 0)} / "
                f"Marengo {tl_analyze.get('marengoCount', 0)}), "
                f"peaks fed: audio={audio_n} chat={chat_n}{vibe_note}"
            )

    analyze = stages.get("analyze") or {}
    if analyze:
        fusion = "yes" if analyze.get("fusionUsed") else "no"
        if not analyze.get("fusionUsed") and int(analyze.get("visualSegmentCount") or 0) == 0:
            decisions.append(
                "Local analyze always runs (librosa + Gemini). "
                "Fusion was skipped because TwelveLabs produced no visual segments."
            )
        gemini = "yes" if analyze.get("geminiUsed") else "no (fallback)"
        model = analyze.get("geminiModel") or analyze.get("analyzeModelTier") or "?"
        decisions.append(
            f"Analyze: {analyze.get('localCandidateCount', 0)} local → "
            f"{analyze.get('fusedCandidateCount', analyze.get('candidateCount', 0))} "
            f"to Gemini, fusion={fusion}, Gemini={gemini} ({model}), "
            f"{analyze.get('highlightCount', 0)} highlights"
        )
        for note in analyze.get("notes") or []:
            if note and note not in decisions:
                decisions.append(f"Note: {note}")

    settings = report.get("projectSettings") or {}
    if not settings.get("vibe"):
        decisions.append(
            "Optimization: no creator vibe set — TwelveLabs queries and Gemini use generic prompts"
        )

    return decisions


def _settings_snapshot(project: Project) -> dict[str, Any]:
    s = project.settings
    return {
        "topN": s.get("topN"),
        "minClipSeconds": s.get("minClipSeconds"),
        "maxClipSeconds": s.get("maxClipSeconds"),
        "vibe": s.get("vibe", ""),
        "analyzeModel": s.get("analyzeModel", "flash"),
        "preRollSeconds": s.get("preRollSeconds"),
        "tailPaddingSeconds": s.get("tailPaddingSeconds"),
        "aspect": s.get("aspect"),
    }
