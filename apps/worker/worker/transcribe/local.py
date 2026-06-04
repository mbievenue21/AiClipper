"""faster-whisper backend — local CPU or GPU transcription.

We cache one loaded WhisperModel at module level so multiple jobs in the same
worker process don't repeatedly pay the ~30s model-load cost. The cache key is
(model_name, device, compute_type); it auto-invalidates if any of those change.

Device selection:
* If CTranslate2 reports any CUDA devices, we use ``cuda`` with the user's
  configured compute type (default float16).
* Otherwise we fall back to ``cpu`` with int8 (fastest CPU option).

Model files are downloaded to the Hugging Face cache on first run
(``~/.cache/huggingface/hub``). Set ``HF_HOME`` to relocate it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from ..config import get_settings
from . import ProgressCb, TranscriptResult, TranscriptSegmentOut

log = structlog.get_logger(__name__)


# Module-level cache. Reset when (model, device, compute_type) changes.
_model: Any = None
_model_signature: tuple[str, str, str] | None = None


def _select_device_compute() -> tuple[str, str]:
    """Pick (device, compute_type) given user settings and detected hardware."""
    settings = get_settings()
    desired_compute = settings.whisper_compute_type or "float16"

    cuda_count = 0
    try:
        import ctranslate2

        cuda_count = ctranslate2.get_cuda_device_count()
    except Exception as exc:  # pragma: no cover — only logged
        log.info("cuda_detect_failed", reason=str(exc))

    if cuda_count > 0:
        return ("cuda", desired_compute)

    # CPU can't do float16 — coerce to int8 for speed.
    cpu_compute = desired_compute
    if cpu_compute in {"float16", "int8_float16"}:
        cpu_compute = "int8"
    return ("cpu", cpu_compute)


def _get_model() -> Any:
    """Return a cached WhisperModel instance, loading on first call."""
    global _model, _model_signature
    settings = get_settings()
    device, compute = _select_device_compute()
    sig = (settings.whisper_model, device, compute)

    if _model is not None and _model_signature == sig:
        return _model

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "faster-whisper is not installed. Run: pnpm --filter worker run setup:transcribe"
        ) from exc

    log.info("whisper_load_start", model=sig[0], device=sig[1], compute_type=sig[2])
    try:
        model = WhisperModel(sig[0], device=sig[1], compute_type=sig[2])
    except Exception as exc:
        # On Windows the CUDA cuBLAS / cuDNN DLLs may be missing even though the
        # GPU is detected. Fall back to CPU+int8 with a clear log message.
        if sig[1] == "cuda":
            log.warning("whisper_cuda_load_failed_falling_back_cpu", reason=str(exc))
            fallback_sig = (sig[0], "cpu", "int8")
            model = WhisperModel(fallback_sig[0], device=fallback_sig[1], compute_type=fallback_sig[2])
            _model = model
            _model_signature = fallback_sig
            log.info("whisper_load_done", **dict(zip(("model", "device", "compute_type"), fallback_sig)))
            return _model
        raise
    _model = model
    _model_signature = sig
    log.info("whisper_load_done", model=sig[0], device=sig[1], compute_type=sig[2])
    return _model


def transcribe_local(
    audio_path: Path,
    *,
    duration_seconds: float | None,
    progress: ProgressCb,
) -> TranscriptResult:
    """Run faster-whisper on ``audio_path`` and return a TranscriptResult."""
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    progress(0.05, "loading whisper model")
    model = _get_model()
    settings = get_settings()
    _, device, compute = (None, *(_model_signature or ("?", "?")))[-3:]

    progress(0.10, f"transcribing on {device} ({compute})")

    # word_timestamps=True is required for Step 9 karaoke captions and Step 8
    # snap-to-word clip boundaries. vad_filter cuts dead air so long VODs go
    # faster and segments are cleaner.
    segments_iter, info = model.transcribe(
        str(audio_path),
        beam_size=5,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
    )

    out_segments: list[TranscriptSegmentOut] = []
    text_parts: list[str] = []
    last_reported = 0.10
    total = duration_seconds if duration_seconds and duration_seconds > 0 else None

    for segment in segments_iter:
        words: list[dict[str, Any]] = []
        for w in segment.words or []:
            words.append(
                {
                    "word": (w.word or "").strip(),
                    "start": float(w.start) if w.start is not None else float(segment.start),
                    "end": float(w.end) if w.end is not None else float(segment.end),
                    "confidence": float(getattr(w, "probability", 1.0) or 0.0),
                }
            )

        seg_text = (segment.text or "").strip()
        out_segments.append(
            TranscriptSegmentOut(
                start_seconds=float(segment.start),
                end_seconds=float(segment.end),
                text=seg_text,
                words=words,
            )
        )
        text_parts.append(seg_text)

        # Throttle progress reports: at most one per 1% delta.
        if total is not None:
            pct = 0.10 + min(0.85, segment.end / total) * 0.85
            if pct - last_reported >= 0.01:
                progress(pct, f"transcribed to {int(segment.end)}s of {int(total)}s")
                last_reported = pct

    progress(0.97, "transcription complete")

    return TranscriptResult(
        language=info.language,
        model_name=f"faster-whisper:{settings.whisper_model}",
        full_text=" ".join(p for p in text_parts if p),
        segments=out_segments,
    )
