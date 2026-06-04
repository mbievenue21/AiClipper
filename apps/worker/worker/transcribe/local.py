"""faster-whisper backend — local CPU or GPU transcription.

We cache one loaded WhisperModel at module level so multiple jobs in the same
worker process don't repeatedly pay the ~30s model-load cost. The cache key is
(model_name, device, compute_type); it auto-invalidates if any of those change.

Device selection:
* ``WHISPER_DEVICE=cpu`` forces CPU (safest on Windows without CUDA Toolkit).
* ``WHISPER_DEVICE=cuda`` forces GPU (fails if cuBLAS/cuDNN are missing).
* ``WHISPER_DEVICE=auto`` (default): use CUDA only when CTranslate2 sees a GPU
  **and** ``cublas64_12.dll`` can actually be loaded (common Windows gap).

Model files are downloaded to the Hugging Face cache on first run
(``~/.cache/huggingface/hub``). ``large-v3`` is ~3 GB — first load can take
several minutes with no UI movement beyond "downloading model".
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

import structlog

from ..config import get_settings
from . import ProgressCb, TranscriptResult, TranscriptSegmentOut

log = structlog.get_logger(__name__)


# Module-level cache. Reset when (model, device, compute_type) changes.
_model: Any = None
_model_signature: tuple[str, str, str] | None = None
_force_cpu: bool = False


def _reset_model_cache(*, force_cpu: bool = False) -> None:
    global _model, _model_signature, _force_cpu
    _model = None
    _model_signature = None
    if force_cpu:
        _force_cpu = True


def _cublas_loadable() -> bool:
    """Return True if cuBLAS can be loaded — required for CUDA inference on Windows."""
    if sys.platform != "win32":
        return True

    import ctypes

    for dll in ("cublas64_12.dll", "cublas64_11.dll"):
        try:
            ctypes.WinDLL(dll)
            return True
        except OSError:
            continue

    for env_key in ("CUDA_PATH", "CUDA_PATH_V12_6", "CUDA_PATH_V12_4", "CUDA_PATH_V12_0"):
        cuda_root = os.environ.get(env_key)
        if not cuda_root:
            continue
        for dll in ("cublas64_12.dll", "cublas64_11.dll"):
            candidate = Path(cuda_root) / "bin" / dll
            if candidate.is_file():
                return True
    return False


def _select_device_compute() -> tuple[str, str]:
    """Pick (device, compute_type) given user settings and detected hardware."""
    settings = get_settings()
    desired_compute = settings.whisper_compute_type or "float16"
    force = settings.whisper_device

    cpu_compute = desired_compute
    if cpu_compute in {"float16", "int8_float16"}:
        cpu_compute = "int8"

    if _force_cpu or force == "cpu":
        return ("cpu", cpu_compute)

    cuda_count = 0
    try:
        import ctranslate2

        cuda_count = ctranslate2.get_cuda_device_count()
    except Exception as exc:  # pragma: no cover
        log.info("cuda_detect_failed", reason=str(exc))

    if force == "cuda":
        if cuda_count <= 0:
            log.warning("whisper_device_cuda_requested_but_no_gpu")
            return ("cpu", cpu_compute)
        if not _cublas_loadable():
            raise RuntimeError(
                "WHISPER_DEVICE=cuda but cublas64_12.dll is not on PATH. "
                "Install CUDA Toolkit 12.x or set WHISPER_DEVICE=cpu in .env."
            )
        return ("cuda", desired_compute)

    # auto
    if cuda_count > 0 and _cublas_loadable():
        return ("cuda", desired_compute)

    if cuda_count > 0 and not _cublas_loadable():
        log.warning(
            "cuda_gpu_detected_but_cublas_missing",
            hint="Using CPU+int8. Install CUDA Toolkit 12 or set WHISPER_DEVICE=cpu.",
        )
    return ("cpu", cpu_compute)


def _load_whisper_model(sig: tuple[str, str, str]) -> Any:
    from faster_whisper import WhisperModel

    settings = get_settings()
    cpu_threads = max(0, int(settings.whisper_cpu_threads))
    # num_workers=1 keeps inference deterministic (the job runner already
    # serializes jobs); a value >1 only helps if you batch multiple files.
    common_kwargs = {"cpu_threads": cpu_threads, "num_workers": 1}

    log.info(
        "whisper_load_start",
        model=sig[0],
        device=sig[1],
        compute_type=sig[2],
        cpu_threads=cpu_threads or "auto",
    )
    try:
        return WhisperModel(sig[0], device=sig[1], compute_type=sig[2], **common_kwargs)
    except Exception as exc:
        if sig[1] == "cuda":
            log.warning("whisper_cuda_load_failed_falling_back_cpu", reason=str(exc))
            fallback_sig = (sig[0], "cpu", "int8")
            model = WhisperModel(
                fallback_sig[0],
                device=fallback_sig[1],
                compute_type=fallback_sig[2],
                **common_kwargs,
            )
            global _model, _model_signature
            _model = model
            _model_signature = fallback_sig
            log.info(
                "whisper_load_done",
                model=fallback_sig[0],
                device=fallback_sig[1],
                compute_type=fallback_sig[2],
            )
            return model
        raise


def _get_model() -> Any:
    """Return a cached WhisperModel instance, loading on first call."""
    global _model, _model_signature
    settings = get_settings()
    device, compute = _select_device_compute()
    sig = (settings.whisper_model, device, compute)

    if _model is not None and _model_signature == sig:
        return _model

    try:
        from faster_whisper import WhisperModel  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "faster-whisper is not installed. Run: pnpm --filter worker run setup:transcribe"
        ) from exc

    model = _load_whisper_model(sig)
    _model = model
    _model_signature = sig
    log.info("whisper_load_done", model=sig[0], device=sig[1], compute_type=sig[2])
    return _model


def _is_cuda_runtime_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "cublas" in msg or "cudnn" in msg or "cuda" in msg and "dll" in msg


def _run_transcribe(model: Any, audio_path: Path) -> tuple[Any, Any]:
    settings = get_settings()
    return model.transcribe(
        str(audio_path),
        # Decoding ----
        # beam_size=1 is greedy and ~2x faster than the default 5; we
        # accept ~1% WER hit because Gemini re-reads the transcript anyway.
        beam_size=max(1, int(settings.whisper_beam_size)),
        best_of=1,
        # Single temperature disables the fallback ladder (Whisper's default
        # tries up to 6 temperatures on low-confidence segments → ~2x slowdown
        # in tricky audio).
        temperature=0.0,
        # Avoids "prior text" hallucination cascades and removes a per-segment
        # serialization dependency, making each chunk independent.
        condition_on_previous_text=False,
        # Language hint skips Whisper's detection pass (~3s saved on long files).
        language=settings.whisper_language,
        # Word-level timing is required for caption alignment (Step 9).
        word_timestamps=True,
        # VAD trims silence before each chunk runs through the model. This is
        # the single biggest speed win on long videos with quiet stretches.
        vad_filter=True,
        vad_parameters={
            "min_silence_duration_ms": int(settings.whisper_vad_silence_ms),
        },
    )


def transcribe_local(
    audio_path: Path,
    *,
    duration_seconds: float | None,
    progress: ProgressCb,
) -> TranscriptResult:
    """Run faster-whisper on ``audio_path`` and return a TranscriptResult."""
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    settings = get_settings()
    model_name = settings.whisper_model

    progress(
        0.05,
        f"loading whisper model '{model_name}' (first run may download ~3 GB)",
    )
    model = _get_model()
    _, device, compute = (None, *(_model_signature or ("?", "?")))[-3:]

    progress(0.10, f"transcribing on {device} ({compute})")

    try:
        segments_iter, info = _run_transcribe(model, audio_path)
    except RuntimeError as exc:
        if not _is_cuda_runtime_error(exc) or device != "cuda":
            raise
        log.warning("whisper_cuda_runtime_failed_falling_back_cpu", reason=str(exc))
        progress(0.08, "CUDA libraries missing — reloading model on CPU (slower)")
        _reset_model_cache(force_cpu=True)
        model = _get_model()
        _, device, compute = (None, *(_model_signature or ("cpu", "int8")))[-3:]
        progress(0.10, f"transcribing on {device} ({compute})")
        segments_iter, info = _run_transcribe(model, audio_path)

    out_segments: list[TranscriptSegmentOut] = []
    text_parts: list[str] = []
    last_reported = 0.10
    total = duration_seconds if duration_seconds and duration_seconds > 0 else None
    inference_started = time.monotonic()

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

        if total is not None:
            pct = 0.10 + min(0.85, segment.end / total) * 0.85
            if pct - last_reported >= 0.01:
                elapsed = time.monotonic() - inference_started
                # Realtime ratio: 2.0 = transcribing twice as fast as audio runs.
                # Anything <1.0 means we're slower than realtime (bad on CPU).
                ratio = (segment.end / elapsed) if elapsed > 0 else 0.0
                remaining = max(0.0, total - segment.end)
                eta = remaining / ratio if ratio > 0 else 0.0
                progress(
                    pct,
                    f"transcribed {int(segment.end)}s/{int(total)}s "
                    f"({ratio:.1f}x realtime, ~{int(eta)}s left)",
                )
                last_reported = pct

    elapsed = time.monotonic() - inference_started
    if total:
        log.info(
            "whisper_inference_done",
            audio_seconds=total,
            wall_seconds=round(elapsed, 1),
            realtime_ratio=round(total / elapsed, 2) if elapsed > 0 else None,
            beam_size=get_settings().whisper_beam_size,
        )
    progress(0.97, "transcription complete")

    return TranscriptResult(
        language=info.language,
        model_name=f"faster-whisper:{model_name}",
        full_text=" ".join(p for p in text_parts if p),
        segments=out_segments,
    )
