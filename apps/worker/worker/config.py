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
    # Multimodal boundary refinement on suspect candidates (costly).
    gemini_multimodal_enabled: bool = Field(
        default=False, alias="GEMINI_MULTIMODAL_ENABLED"
    )
    # Gemini model IDs. Defaults track the newest Gemini 3.x releases.
    # Docs: https://ai.google.dev/gemini-api/docs/models
    #   - gemini-3.5-flash       : newest STABLE Flash (frontier, agentic) — default
    #   - gemini-3.1-pro-preview : newest Pro (deepest reasoning, preview)
    #   - gemini-3.1-flash-lite  : cheapest stable, high-volume
    gemini_pro_model: str = Field(
        default="gemini-3.1-pro-preview", alias="GEMINI_PRO_MODEL"
    )
    gemini_flash_model: str = Field(
        default="gemini-3.5-flash", alias="GEMINI_FLASH_MODEL"
    )
    gemini_flash_lite_model: str = Field(
        default="gemini-3.1-flash-lite", alias="GEMINI_FLASH_LITE_MODEL"
    )
    # Model used for the optional multimodal boundary-refinement pass.
    gemini_multimodal_model: str = Field(
        default="gemini-3.1-pro-preview", alias="GEMINI_MULTIMODAL_MODEL"
    )
    # Gemini 3.x reasoning effort: minimal | low | medium | high.
    # "low" is the sweet spot for highlight ranking (analysis-grade, fast/cheap).
    gemini_thinking_level: str = Field(
        default="low", alias="GEMINI_THINKING_LEVEL"
    )

    # ---- AI: optional audio enrichment (AssemblyAI / Deepgram) ------------
    enrichment_backend: str | None = Field(default=None, alias="ENRICHMENT_BACKEND")
    assemblyai_api_key: str | None = Field(default=None, alias="ASSEMBLYAI_API_KEY")
    deepgram_api_key: str | None = Field(default=None, alias="DEEPGRAM_API_KEY")

    # ---- TwelveLabs video-native multimodal analysis ----------------------
    twelvelabs_enabled: bool = Field(default=False, alias="TWELVELABS_ENABLED")
    twelvelabs_api_key: str | None = Field(default=None, alias="TWELVELABS_API_KEY")
    twelvelabs_index_id: str | None = Field(default=None, alias="TWELVELABS_INDEX_ID")
    twelvelabs_model_marengo: str = Field(
        default="marengo3.0", alias="TWELVELABS_MODEL_MARENGO"
    )
    twelvelabs_model_pegasus: str = Field(
        default="pegasus1.5", alias="TWELVELABS_MODEL_PEGASUS"
    )
    twelvelabs_max_analyze_chunk_seconds: int = Field(
        default=7200, alias="TWELVELABS_MAX_ANALYZE_CHUNK_SECONDS"
    )
    twelvelabs_pegasus_chunk_seconds: int = Field(
        default=1200,
        alias="TWELVELABS_PEGASUS_CHUNK_SECONDS",
        description="Max seconds per Pegasus analyze window (smaller = faster, less timeout risk).",
    )
    twelvelabs_chunk_overlap_seconds: int = Field(
        default=15, alias="TWELVELABS_CHUNK_OVERLAP_SECONDS"
    )
    twelvelabs_max_search_results_per_query: int = Field(
        default=10, alias="TWELVELABS_MAX_SEARCH_RESULTS_PER_QUERY"
    )
    twelvelabs_visual_candidate_limit: int = Field(
        default=40, alias="TWELVELABS_VISUAL_CANDIDATE_LIMIT"
    )
    twelvelabs_min_visual_confidence: float = Field(
        default=0.55, alias="TWELVELABS_MIN_VISUAL_CONFIDENCE"
    )
    twelvelabs_upload_full_video: bool = Field(
        default=True, alias="TWELVELABS_UPLOAD_FULL_VIDEO"
    )
    twelvelabs_reuse_existing_index: bool = Field(
        default=True, alias="TWELVELABS_REUSE_EXISTING_INDEX"
    )
    twelvelabs_fail_open: bool = Field(default=True, alias="TWELVELABS_FAIL_OPEN")
    # TwelveLabs POST /tasks rejects files >= 2 GB — stay under with margin.
    twelvelabs_max_upload_bytes: int = Field(
        default=1_900_000_000, alias="TWELVELABS_MAX_UPLOAD_BYTES"
    )

    # ---- Publishing: YouTube OAuth (app credentials, not per-channel) ----
    youtube_client_id: str = Field(default="", alias="YOUTUBE_CLIENT_ID")
    youtube_client_secret: str = Field(default="", alias="YOUTUBE_CLIENT_SECRET")

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
