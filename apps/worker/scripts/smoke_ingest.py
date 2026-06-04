"""Verifies ingest handler is registered (no network download).

Run: .venv\\Scripts\\python scripts\\smoke_ingest.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worker.jobs import ingest as _ingest  # noqa: F401
from worker.jobs.handlers import get_handler, registered_types


def main() -> None:
    types = registered_types()
    print("Registered job types:", ", ".join(types) or "(none)")
    handler = get_handler("ingest")
    if handler is None:
        print("FAIL: ingest handler not registered")
        sys.exit(1)
    print("OK — ingest handler:", handler.__name__)


if __name__ == "__main__":
    main()
