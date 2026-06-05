"""Optional Tier-2 audio intelligence enrichment (AssemblyAI / Deepgram).

Runs as a parallel pass on audio.wav when configured. Produces timestamped
events (sentiment shifts, laughter, etc.) that can seed additional candidates.

Set ENRICHMENT_BACKEND=assemblyai|deepgram and the corresponding API key.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from ..config import get_settings

log = structlog.get_logger(__name__)


@dataclass
class EnrichmentEvent:
    start_seconds: float
    end_seconds: float
    label: str
    confidence: float


@dataclass
class EnrichmentResult:
    backend: str
    events: list[EnrichmentEvent]


def is_enrichment_configured() -> bool:
    s = get_settings()
    backend = (s.enrichment_backend or "").strip().lower()
    if backend == "assemblyai":
        return bool(s.assemblyai_api_key)
    if backend == "deepgram":
        return bool(s.deepgram_api_key)
    return False


def run_enrichment(audio_path: Path) -> EnrichmentResult | None:
    """Best-effort enrichment; returns None if not configured or on failure."""
    settings = get_settings()
    backend = (settings.enrichment_backend or "").strip().lower()
    if not backend:
        return None

    try:
        if backend == "assemblyai" and settings.assemblyai_api_key:
            return _assemblyai_pass(audio_path, settings.assemblyai_api_key)
        if backend == "deepgram" and settings.deepgram_api_key:
            return _deepgram_pass(audio_path, settings.deepgram_api_key)
    except Exception as exc:
        log.warning("enrichment_failed", backend=backend, error=str(exc))
    return None


def _assemblyai_pass(audio_path: Path, api_key: str) -> EnrichmentResult:
    import httpx

    log.info("enrichment_assemblyai_start", path=str(audio_path))
    with open(audio_path, "rb") as f:
        upload_resp = httpx.post(
            "https://api.assemblyai.com/v2/upload",
            headers={"authorization": api_key},
            content=f.read(),
            timeout=120.0,
        )
        upload_resp.raise_for_status()
        upload_url = upload_resp.json()["upload_url"]

    transcript_resp = httpx.post(
        "https://api.assemblyai.com/v2/transcript",
        headers={"authorization": api_key},
        json={
            "audio_url": upload_url,
            "auto_chapters": True,
            "sentiment_analysis": True,
        },
        timeout=60.0,
    )
    transcript_resp.raise_for_status()
    transcript_id = transcript_resp.json()["id"]

    import time

    status = "queued"
    data: dict[str, Any] = {}
    for _ in range(120):
        poll = httpx.get(
            f"https://api.assemblyai.com/v2/transcript/{transcript_id}",
            headers={"authorization": api_key},
            timeout=30.0,
        )
        poll.raise_for_status()
        data = poll.json()
        status = data.get("status", "")
        if status == "completed":
            break
        if status == "error":
            raise RuntimeError(data.get("error", "assemblyai error"))
        time.sleep(2.0)
    else:
        raise TimeoutError("assemblyai transcript timed out")

    events: list[EnrichmentEvent] = []
    for ch in data.get("chapters") or []:
        events.append(
            EnrichmentEvent(
                start_seconds=float(ch.get("start", 0)) / 1000.0,
                end_seconds=float(ch.get("end", 0)) / 1000.0,
                label=str(ch.get("gist") or ch.get("headline") or "chapter"),
                confidence=0.7,
            )
        )
    for sent in data.get("sentiment_analysis_results") or []:
        if sent.get("sentiment") in ("POSITIVE", "NEGATIVE"):
            events.append(
                EnrichmentEvent(
                    start_seconds=float(sent.get("start", 0)) / 1000.0,
                    end_seconds=float(sent.get("end", 0)) / 1000.0,
                    label=f"sentiment_{str(sent.get('sentiment', '')).lower()}",
                    confidence=float(sent.get("confidence", 0.5)),
                )
            )

    log.info("enrichment_assemblyai_done", events=len(events))
    return EnrichmentResult(backend="assemblyai", events=events)


def _deepgram_pass(audio_path: Path, api_key: str) -> EnrichmentResult:
    import httpx

    log.info("enrichment_deepgram_start", path=str(audio_path))
    with open(audio_path, "rb") as f:
        resp = httpx.post(
            "https://api.deepgram.com/v1/listen",
            params={
                "model": "nova-2",
                "utterances": "true",
                "sentiment": "true",
            },
            headers={
                "Authorization": f"Token {api_key}",
                "Content-Type": "audio/wav",
            },
            content=f.read(),
            timeout=300.0,
        )
        resp.raise_for_status()
        data = resp.json()

    events: list[EnrichmentEvent] = []
    utterances = (
        data.get("results", {})
        .get("utterances", [])
    )
    for utt in utterances:
        sentiment = (utt.get("sentiment") or "").lower()
        if sentiment in ("positive", "negative"):
            events.append(
                EnrichmentEvent(
                    start_seconds=float(utt.get("start", 0)),
                    end_seconds=float(utt.get("end", 0)),
                    label=f"sentiment_{sentiment}",
                    confidence=0.65,
                )
            )

    log.info("enrichment_deepgram_done", events=len(events))
    return EnrichmentResult(backend="deepgram", events=events)
