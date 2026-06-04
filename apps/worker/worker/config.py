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
    # Defaults to `small` (244M params) — runs at ~5-7x realtime on a modern
    # CPU and produces transcripts good enough for highlight ranking. Bump to
    # `medium` or `large-v3` only if you have a CUDA GPU.
    whisper_model: str = Field(default="small", alias="WHISPER_MODEL")
    whisper_compute_type: str = Field(
        default="float16", alias="WHISPER_COMPUTE_TYPE"
    )
    # auto = use GPU only if cuBLAS is loadable; cpu = always CPU; cuda = require GPU
    whisper_device: Literal["auto", "cuda", "cpu"] = Field(
        default="auto", alias="WHISPER_DEVICE"
    )
    # Beam size: 1 is greedy decoding (~2x faster than beam=5, ~1% WER hit on
    # clean English). Keep this at 1 unless you're doing accent-heavy content.
    whisper_beam_size: int = Field(default=1, alias="WHISPER_BEAM_SIZE")
    # Optional language hint (e.g. "en"). Skips Whisper's language detection
    # pass — saves ~2-5s on long videos.
    whisper_language: str | None = Field(default=None, alias="WHISPER_LANGUAGE")
    # CPU threads for CTranslate2. 0 = auto (uses OMP_NUM_THREADS or all cores).
    whisper_cpu_threads: int = Field(default=0, alias="WHISPER_CPU_THREADS")
    # VAD min silence to trim. 700ms cuts ~30% of speech gaps; lower if your
    # content has rapid back-and-forth dialogue you don't want clipped.
    whisper_vad_silence_ms: int = Field(default=700, alias="WHISPER_VAD_SILENCE_MS")
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
