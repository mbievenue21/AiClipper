"""GET /health — quick status + capability report."""

from __future__ import annotations

import shutil
import sys
from importlib import metadata
from pathlib import Path

from fastapi import APIRouter
from sqlalchemy import text

from ..config import get_settings
from ..db import get_engine
from ..jobs import handlers

router = APIRouter()


def _check_module(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


@router.get("/health")
def health() -> dict:
    settings = get_settings()

    db_ok = False
    db_error: str | None = None
    try:
        with get_engine().connect() as conn:
            value = conn.execute(text("SELECT 1")).scalar_one()
            db_ok = value == 1
    except Exception as exc:  # pragma: no cover
        db_error = str(exc)

    db_path = settings.database_path

    capabilities = {
        "ingest": shutil.which("yt-dlp") is not None and shutil.which("ffmpeg") is not None,
        "transcribe_local": _check_module("faster-whisper") is not None,
        "transcribe_groq": _check_module("groq") is not None,
        "analyze": _check_module("librosa") is not None
        and _check_module("scenedetect") is not None
        and _check_module("google-genai") is not None,
        "render": _check_module("mediapipe") is not None,
    }

    return {
        "status": "ok" if db_ok else "degraded",
        "python_version": sys.version.split(" ", 1)[0],
        "database": {
            "path": str(db_path),
            "exists": Path(db_path).exists(),
            "ok": db_ok,
            "error": db_error,
        },
        "media_root": str(settings.media_root_path),
        "registered_handlers": handlers.registered_types(),
        "capabilities": capabilities,
    }
