"""Step 7 smoke test — verifies analyze wiring WITHOUT running the pipeline.

Checks:
  1. ``analyze`` handler is in the job registry.
  2. librosa / scenedetect / google-genai are importable.
  3. Settings are present and GEMINI_API_KEY status is reported.

Run::

    .venv\\Scripts\\python scripts\\smoke_analyze.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worker.config import get_settings  # noqa: E402
from worker.jobs import analyze as _analyze_job  # noqa: E402,F401
from worker.jobs.handlers import get_handler, registered_types  # noqa: E402


def main() -> None:
    settings = get_settings()
    print(f"Registered job types: {', '.join(registered_types()) or '(none)'}")
    if get_handler("analyze") is None:
        print("FAIL: analyze handler not registered")
        sys.exit(1)
    print("OK analyze handler registered")

    try:
        import librosa  # noqa: F401
    except ImportError:
        print("FAIL: librosa not installed. Run: pnpm --filter worker run setup:analyze")
        sys.exit(1)
    print(f"OK librosa importable (version={librosa.__version__})")

    try:
        import scenedetect  # noqa: F401
    except ImportError:
        print("FAIL: scenedetect not installed.")
        sys.exit(1)
    print(f"OK scenedetect importable (version={scenedetect.__version__})")

    try:
        from google import genai  # noqa: F401
        from google.genai import types  # noqa: F401
    except ImportError:
        print("FAIL: google-genai not installed.")
        sys.exit(1)
    print("OK google-genai importable")

    if settings.gemini_api_key:
        print(f"OK GEMINI_API_KEY set (len={len(settings.gemini_api_key)})")
    else:
        print(
            "WARN GEMINI_API_KEY not set — analyze will fall back to local "
            "scoring only. Set it in .env to enable LLM rerank."
        )

    print("OK Step 7 smoke test passed")


if __name__ == "__main__":
    main()
