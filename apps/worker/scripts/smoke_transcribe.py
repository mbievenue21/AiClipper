"""Step 6 smoke test — verifies transcribe wiring WITHOUT downloading models.

Checks:
  1. ``transcribe`` handler is in the job registry.
  2. faster-whisper is importable (only if TRANSCRIBE_BACKEND=local).
  3. Detected device + compute type for the current settings.

Run::

    .venv\\Scripts\\python scripts\\smoke_transcribe.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worker.config import get_settings  # noqa: E402
from worker.jobs import transcribe as _transcribe_job  # noqa: E402,F401
from worker.jobs.handlers import get_handler, registered_types  # noqa: E402


def main() -> None:
    settings = get_settings()
    print(f"Backend (TRANSCRIBE_BACKEND): {settings.transcribe_backend}")
    print(f"Whisper model (WHISPER_MODEL): {settings.whisper_model}")
    print(f"Compute type (WHISPER_COMPUTE_TYPE): {settings.whisper_compute_type}")
    print(f"Registered job types: {', '.join(registered_types()) or '(none)'}")

    if get_handler("transcribe") is None:
        print("FAIL: transcribe handler not registered")
        sys.exit(1)
    print("OK transcribe handler registered")

    if settings.transcribe_backend == "local":
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            print("FAIL: faster-whisper not installed. Run: pnpm --filter worker run setup:transcribe")
            sys.exit(1)
        print(f"OK faster-whisper importable (version={faster_whisper.__version__})")

        from worker.transcribe.local import _select_device_compute

        device, compute = _select_device_compute()
        print(f"Detected: device={device} compute_type={compute}")
        if device == "cpu":
            print("Note: CUDA not detected (or cuBLAS/cuDNN unavailable). Transcription will use CPU.")
    else:
        print(f"Skipping local import check (backend={settings.transcribe_backend})")

    print("OK Step 6 smoke test passed")


if __name__ == "__main__":
    main()
