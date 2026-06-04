"""Thin ffmpeg subprocess helpers.

We deliberately run ffmpeg through sync `subprocess` inside `asyncio.to_thread`
rather than `asyncio.create_subprocess_exec`. On Windows the asyncio subprocess
API only works on the Proactor event loop; running it in a worker thread sidesteps
that and works on any platform / loop type.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path


def _run_ffmpeg(cmd: list[str], *, what: str) -> None:
    """Run an ffmpeg command, raising RuntimeError with stderr on non-zero exit."""
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        msg = result.stderr.decode(errors="replace").strip() or f"ffmpeg exited {result.returncode}"
        raise RuntimeError(f"{what} failed: {msg}")


def _extract_mono_wav_sync(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(dest),
        ],
        what="Audio extract",
    )


async def extract_mono_wav(source: Path, dest: Path) -> None:
    """16 kHz mono PCM WAV for downstream Whisper / librosa (Step 6+)."""
    await asyncio.to_thread(_extract_mono_wav_sync, source, dest)


def _compress_to_opus_sync(source: Path, dest: Path, *, bitrate_kbps: int) -> None:
    """Transcode any audio file to Opus-in-Ogg at the requested bitrate.

    Speech at 32 kbps Opus mono 16 kHz is roughly 240 KB/min — a 14-minute clip
    drops from a 25 MB WAV to ~3.4 MB. Used for the Groq audio upload path
    where the 25 MB request limit is the bottleneck.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-c:a",
            "libopus",
            "-b:a",
            f"{bitrate_kbps}k",
            # VBR application=voip biases the encoder toward speech intelligibility.
            "-vbr",
            "on",
            "-application",
            "voip",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(dest),
        ],
        what="Audio compress",
    )


async def compress_to_opus(source: Path, dest: Path, *, bitrate_kbps: int = 32) -> None:
    """Async wrapper around the Opus compress helper."""
    await asyncio.to_thread(_compress_to_opus_sync, source, dest, bitrate_kbps=bitrate_kbps)


def _split_audio_sync(
    source: Path,
    out_dir: Path,
    *,
    chunk_seconds: int,
    bitrate_kbps: int,
) -> list[Path]:
    """Split `source` into Opus chunks of `chunk_seconds` each.

    Uses ffmpeg's `-f segment` muxer which writes consecutively numbered files
    and snaps cuts to keyframes (for Opus, every frame is a keyframe so cuts
    are exact). Returns the produced files sorted lexicographically — which is
    the same as time-ascending because we use 4-digit zero-padded numbers.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = out_dir / "chunk_%04d.ogg"
    _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-c:a",
            "libopus",
            "-b:a",
            f"{bitrate_kbps}k",
            "-vbr",
            "on",
            "-application",
            "voip",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-f",
            "segment",
            "-segment_time",
            str(chunk_seconds),
            "-reset_timestamps",
            "1",
            str(pattern),
        ],
        what="Audio split",
    )
    return sorted(out_dir.glob("chunk_*.ogg"))


async def split_audio_to_opus_chunks(
    source: Path,
    out_dir: Path,
    *,
    chunk_seconds: int = 1500,  # 25 min — ~7 MB at 32 kbps Opus, well under 25 MB
    bitrate_kbps: int = 32,
) -> list[Path]:
    """Async wrapper: split `source` into time-bounded Opus chunks."""
    return await asyncio.to_thread(
        _split_audio_sync,
        source,
        out_dir,
        chunk_seconds=chunk_seconds,
        bitrate_kbps=bitrate_kbps,
    )
