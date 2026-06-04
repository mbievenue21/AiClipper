"""FastAPI entry point for the AiClipper worker.

Run with::

    pnpm --filter worker dev          # uvicorn worker.main:app --reload --port 8000
    # or
    python -m uvicorn worker.main:app --reload --port 8000

The lifespan starts the background job runner so HTTP requests and
job execution share one process.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import AsyncIterator

# On Windows, uvicorn's reload mode can leave the worker on a SelectorEventLoop,
# which cannot launch subprocesses (NotImplementedError in _make_subprocess_transport).
# Force the Proactor policy so any code that does use asyncio subprocess still works.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import health as health_api
from .api import jobs as jobs_api
from . import jobs as _jobs  # noqa: F401 — register handlers
from .jobs.runner import runner
from .logging import configure_logging, get_logger

configure_logging("INFO")
log = get_logger(__name__)


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    log.info("worker_starting")
    await runner.start()
    try:
        yield
    finally:
        log.info("worker_stopping")
        await runner.stop()


app = FastAPI(
    title="AiClipper Worker",
    version="0.1.0",
    lifespan=lifespan,
)

# Next.js dev server runs on :3000.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_api.router)
app.include_router(jobs_api.router)


def run_cli() -> None:
    """Entry point installed as `aiclipper-worker` console script."""
    uvicorn.run("worker.main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    run_cli()
