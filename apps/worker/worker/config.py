"""Settings loaded from environment variables / .env file.

Mirrors the keys documented in the repo-root .env.example. Falls back to
sane defaults so the worker can start without any external API keys
configured (you only need keys for the features you actually use).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root = three levels up from this file:
#   apps/worker/worker/config.py -> apps/worker/worker -> apps/worker -> apps -> repo
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- Core paths -------------------------------------------------------
    database_url: str = Field(default="file:./data/app.db", alias="DATABASE_URL")
    media_root: str = Field(default="./data/videos", alias="MEDIA_ROOT")

    # ---- AI: transcription ------------------------------------------------
    transcribe_backend: Literal["local", "groq"] = Field(
        default="local", alias="TRANSCRIBE_BACKEND"
    )
    whisper_model: str = Field(default="large-v3", alias="WHISPER_MODEL")
    whisper_compute_type: str = Field(
        default="float16", alias="WHISPER_COMPUTE_TYPE"
    )
    # auto = use GPU only if cuBLAS is loadable; cpu = always CPU; cuda = require GPU
    whisper_device: Literal["auto", "cuda", "cpu"] = Field(
        default="auto", alias="WHISPER_DEVICE"
    )
    groq_api_key: str | None = Field(default=None, alias="GROQ_API_KEY")

    # ---- AI: highlights / thumbnails -------------------------------------
    gemini_api_key: str | None = Field(default=None, alias="GEMINI_API_KEY")
    fal_api_key: str | None = Field(default=None, alias="FAL_API_KEY")

    # ---- Job loop tuning --------------------------------------------------
    job_poll_interval_seconds: float = Field(default=1.0)
    job_max_concurrent: int = Field(default=1)  # raise if you have multiple GPUs

    @property
    def database_path(self) -> Path:
        raw = self.database_url
        if raw.startswith("file:"):
            raw = raw[len("file:") :]
        path = Path(raw)
        if not path.is_absolute():
            path = (REPO_ROOT / path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def media_root_path(self) -> Path:
        path = Path(self.media_root)
        if not path.is_absolute():
            path = (REPO_ROOT / path).resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
