"""Pre-download + warm the faster-whisper model so the first project doesn't
pay the model-load cost mid-job.

Reads the same WHISPER_* env vars the live worker uses, so what you warm is
exactly what you'll get in production. Idempotent — safe to run repeatedly.

Run from apps/worker:

    .venv\\Scripts\\python scripts\\warm_whisper.py
    # or
    pnpm --filter worker run whisper:warm
"""

from __future__ import annotations

import sys
import time

from worker.config import get_settings
from worker.transcribe.local import _get_model, _select_device_compute


def main() -> int:
    settings = get_settings()
    device, compute = _select_device_compute()
    print(
        f"[warm] model={settings.whisper_model} device={device} compute={compute} "
        f"beam={settings.whisper_beam_size} cpu_threads="
        f"{settings.whisper_cpu_threads or 'auto'}"
    )
    print("[warm] loading (first time downloads weights to ~/.cache/huggingface/hub)...")
    t0 = time.monotonic()
    _ = _get_model()
    dt = time.monotonic() - t0
    print(f"[warm] loaded in {dt:.1f}s — next job's first transcribe pays $0.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
