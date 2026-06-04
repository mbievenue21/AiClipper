# AiClipper Worker

Python FastAPI service responsible for the heavy lifting:
download → transcribe → analyze → render → publish.

Reads/writes the same SQLite file as the Next.js app (`../../data/app.db`).

## Setup

```powershell
# From the repo root (`setup` is a pnpm script — use `run` to avoid the built-in `pnpm setup` command)
pnpm --filter worker run setup
pnpm --filter worker run setup:ingest
pnpm --filter worker run setup:transcribe   # Step 6
```

This creates `.venv`, installs core FastAPI/SQLAlchemy deps, then the `[ingest]`
extra (`yt-dlp`, `ffmpeg-python`), and finally `[transcribe]`
(`faster-whisper`, which pulls `ctranslate2` and Whisper tokenizers).

**Step 5 (ingest)** also requires system **`yt-dlp`**, **`ffmpeg`**, and
**`ffprobe`** on your PATH (see root README for `winget` install).

**Step 6 (transcribe)** uses faster-whisper. CUDA is auto-detected; if cuBLAS /
cuDNN aren't available the worker falls back to CPU + int8. Model weights
download to `~/.cache/huggingface/hub` on first transcribe.

Other heavy deps stay in optional extras (`[transcribe]`, `[analyze]`, …)
until those pipeline steps are enabled — see `setup:*` in `package.json`.

## Running

```powershell
pnpm --filter worker dev   # uvicorn with --reload
```

Then: <http://127.0.0.1:8000/health>
