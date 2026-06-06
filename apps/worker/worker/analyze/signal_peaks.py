"""Shared audio/chat peak detection for TwelveLabs prompts and local candidates."""

from __future__ import annotations

from pathlib import Path

from .audio_features import compute_audio_features
from .chat_features import ChatEventOut, compute_chat_density


def find_peak_indices(
    values: list[float],
    *,
    min_height: float,
    min_distance: int,
) -> list[int]:
    """Simple peak finder without scipy."""
    if not values:
        return []
    peaks: list[int] = []
    for i in range(1, len(values) - 1):
        if values[i] < min_height:
            continue
        if values[i] <= values[i - 1] or values[i] < values[i + 1]:
            continue
        if peaks and (i - peaks[-1]) < min_distance:
            if values[i] > values[peaks[-1]]:
                peaks[-1] = i
            continue
        peaks.append(i)
    return peaks


def collect_signal_peak_times(
    *,
    audio_path: Path,
    chat_events: list[ChatEventOut],
    duration_seconds: float,
    min_clip_seconds: float = 20.0,
    max_peaks: int = 12,
) -> tuple[list[float], list[float]]:
    """Return top audio excitement and chat density peak timestamps (seconds)."""
    audio_series = compute_audio_features(audio_path)
    duration = duration_seconds or audio_series.duration_seconds
    chat_density = compute_chat_density(chat_events, duration_seconds=duration)

    min_distance = max(1, int(min_clip_seconds))
    excitement = [s.get("excitement", 0.0) for s in audio_series.samples]
    audio_peaks = [
        float(idx)
        for idx in find_peak_indices(
            excitement,
            min_height=0.55,
            min_distance=min_distance,
        )
    ]
    chat_peaks = [
        float(idx)
        for idx in find_peak_indices(
            chat_density.normalised,
            min_height=0.5,
            min_distance=min_distance,
        )
    ]

    audio_peaks.sort(
        key=lambda t: excitement[int(t)] if int(t) < len(excitement) else 0.0,
        reverse=True,
    )
    chat_peaks.sort(
        key=lambda t: chat_density.normalised[int(t)]
        if int(t) < len(chat_density.normalised)
        else 0.0,
        reverse=True,
    )
    return audio_peaks[:max_peaks], chat_peaks[:max_peaks]


def peaks_in_range(
    peaks: list[float],
    start_seconds: float,
    end_seconds: float,
    *,
    offset_seconds: float = 0.0,
) -> list[float]:
    """Filter peaks to a time range and optionally re-base timestamps."""
    if end_seconds <= start_seconds:
        return []
    return [
        t - offset_seconds
        for t in peaks
        if start_seconds <= t < end_seconds
    ]
