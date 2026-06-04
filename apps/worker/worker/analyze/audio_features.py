"""Per-second audio "excitement" curve via librosa.

Combines short-term RMS energy (volume) with onset strength (transients /
percussive content) and normalises each into a 0..1 score over the whole
clip. The blended signal lights up on laughter, cheering, shouting,
sudden hits, and music drops — the moments that usually anchor highlights.

We downsample to 1 Hz so the resulting array is small (one float per second)
and easy to align against transcript timestamps. The full per-second series
is persisted to the ``audio_features`` table as JSON.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import structlog

log = structlog.get_logger(__name__)


@dataclass
class AudioFeatureSeries:
    samples: list[dict[str, float]]  # [{"t": float_sec, "rmsDb": float, "excitement": float}]
    duration_seconds: float

    def excitement_at(self, t: float) -> float:
        idx = max(0, min(len(self.samples) - 1, int(round(t))))
        return float(self.samples[idx]["excitement"])

    def excitement_window(self, start: float, end: float) -> float:
        if end <= start or not self.samples:
            return 0.0
        i = max(0, int(round(start)))
        j = max(i + 1, min(len(self.samples), int(round(end)) + 1))
        slice_ = [s["excitement"] for s in self.samples[i:j]]
        return float(sum(slice_) / len(slice_)) if slice_ else 0.0


def compute_audio_features(audio_path: Path) -> AudioFeatureSeries:
    """Return one sample per second of audio.

    Heavy: this can take ~1–5 s per minute of audio on CPU. Call from a
    thread (``asyncio.to_thread``) when invoked from async code.
    """
    if not audio_path.exists():
        raise FileNotFoundError(audio_path)

    # Defer import so the analyze module doesn't blow up at module-load if
    # librosa is not installed (smoke tests, etc.).
    import librosa

    log.info("audio_features_start", audio_path=str(audio_path))

    # Resample to 22050 Hz mono — plenty for energy/onset, ~4x less data
    # than the 16k WAV we get from ingest at half the precision cost.
    y, sr = librosa.load(str(audio_path), sr=22050, mono=True)
    duration_s = float(librosa.get_duration(y=y, sr=sr))

    # Frame parameters: ~46 ms hops give us ~21 frames per second.
    hop_length = 1024
    frame_length = 2048

    rms = librosa.feature.rms(y=y, frame_length=frame_length, hop_length=hop_length)[0]
    # Convert to decibels (relative). Floor at -80 dB to avoid -inf.
    rms_db = librosa.amplitude_to_db(np.maximum(rms, 1e-6), ref=np.max)

    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length)

    # Per-frame timestamps in seconds.
    frame_times = librosa.frames_to_time(np.arange(len(rms_db)), sr=sr, hop_length=hop_length)

    # Bin into per-second buckets and take the mean.
    n_seconds = max(1, int(np.ceil(duration_s)))
    rms_db_per_s = np.full(n_seconds, -80.0, dtype=np.float32)
    onset_per_s = np.zeros(n_seconds, dtype=np.float32)
    counts = np.zeros(n_seconds, dtype=np.int32)
    for i, t in enumerate(frame_times):
        idx = min(n_seconds - 1, int(t))
        counts[idx] += 1
        rms_db_per_s[idx] = (rms_db_per_s[idx] * (counts[idx] - 1) + rms_db[i]) / counts[idx]
        onset_per_s[idx] = (onset_per_s[idx] * (counts[idx] - 1) + onset[i]) / counts[idx]

    # Normalise each track to 0..1 across the whole clip, then blend.
    def _norm01(x: np.ndarray) -> np.ndarray:
        lo, hi = float(np.percentile(x, 5)), float(np.percentile(x, 95))
        if hi - lo < 1e-6:
            return np.zeros_like(x, dtype=np.float32)
        out = (x - lo) / (hi - lo)
        return np.clip(out, 0.0, 1.0).astype(np.float32)

    rms_norm = _norm01(rms_db_per_s)
    onset_norm = _norm01(onset_per_s)

    # 60% volume, 40% transient activity. Tuned for talking-head + reaction
    # streams; pure music videos would want the inverse weighting.
    excitement = (0.6 * rms_norm + 0.4 * onset_norm).clip(0.0, 1.0)

    samples = [
        {
            "t": float(i),
            "rmsDb": float(rms_db_per_s[i]),
            "excitement": float(excitement[i]),
        }
        for i in range(n_seconds)
    ]

    log.info(
        "audio_features_done",
        seconds=n_seconds,
        mean_excitement=float(excitement.mean()),
        peak_excitement=float(excitement.max()),
    )
    return AudioFeatureSeries(samples=samples, duration_seconds=duration_s)
