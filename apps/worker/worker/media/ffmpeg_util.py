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


def _extract_mono_wav_sync(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
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
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        msg = result.stderr.decode(errors="replace").strip() or f"ffmpeg exited {result.returncode}"
        raise RuntimeError(f"Audio extract failed: {msg}")


async def extract_mono_wav(source: Path, dest: Path) -> None:
    """16 kHz mono PCM WAV for downstream Whisper / librosa (Step 6+)."""
    await asyncio.to_thread(_extract_mono_wav_sync, source, dest)
