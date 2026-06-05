# AiClipper

Personal-use app: paste a long-form VOD URL, run a staged pipeline (download → transcribe → optional TwelveLabs visual → Gemini-ranked highlights → render clips with captions → schedule YouTube / Instagram uploads). Monorepo, SQLite, mostly local media processing.

---

## Status at a glance

### Working

| Area | Notes |
|------|--------|
| **Ingest** | yt-dlp download, ffprobe, `audio.wav` extraction |
| **Transcribe** | faster-whisper (local GPU/CPU) or Groq Whisper API |
| **TwelveLabs index** | v1.3 assets API, multipart upload, ffmpeg chunking for files &gt; ~2 GB |
| **TwelveLabs visual (partial)** | Pegasus segmentation on shorter chunks; visual segments fused into highlight candidates |
| **Analyze** | librosa audio, optional chat density, PySceneDetect cuts, candidate fusion, **Gemini 3.x** rerank |
| **Highlights UI** | Top-N candidates with scores, titles, signal breakdown |
| **Render + captions** | FFmpeg cut/reformat, libass caption burn-in |
| **Clip editor** | Trim timeline, caption segment edits, re-render via `reedit` job |
| **Live progress** | SSE on project page + pipeline stage pills |
| **Publish** | YouTube OAuth + Instagram Reels (scheduled or immediate) |
| **Ops** | Project delete (DB + disk), `/admin` cleanup, stuck-job heal |

### Known issues / gaps

| Issue | What’s going on |
|-------|------------------|
| **Pegasus timeout** | Sync `POST /analyze` on long chunks (~28 min of video) exceeds the worker’s **120 s HTTP read timeout**. Shorter tail chunks succeed; long first chunk fails open to Marengo-only. **Fix needed:** async `/analyze/tasks` for long windows and/or a longer analyze timeout. |
| **Marengo search (0 hits)** | `POST /search` runs (23 queries/chunk) but often returns **0 hits** in practice. Pipeline still works via Pegasus + local signals; Marengo fusion is weak until queries/index tuning is improved. |
| **Analysis speed** | Long VODs are slow end-to-end: TL **multipart upload** (15–30+ min for ~1.7 GB), **scene detection** (~5 min on 36 min VOD), TL indexing poll, then Gemini. Expect **30–45+ min** for a 2 GB / ~36 min stream with TwelveLabs enabled. |
| **Chat signal** | Some yt-dlp builds lack `--write-chat`; chat density stays zero (logged, non-fatal). |
| **Thumbnails** | Frame + Satori step not implemented (intentionally skipped). |

---

## Pipeline flow (ingest → highlights)

Default job chain when `TWELVELABS_ENABLED=true`. Without TwelveLabs, transcribe enqueues `analyze` directly.

```
User URL (UI)
    │
    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 1. INGEST          job: ingest                                            │
│    yt-dlp → source.mp4    ffmpeg/ffprobe → audio.wav (16 kHz mono)       │
│    Stack: yt-dlp, FFmpeg, Python worker (FastAPI job loop)               │
└─────────────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 2. TRANSCRIBE      job: transcribe                                        │
│    Local: faster-whisper (CTranslate2, CUDA or CPU)                       │
│    Or: Groq API — whisper-large-v3                                       │
│    Out: transcripts + transcript_segments (word timestamps)             │
└─────────────────────────────────────────────────────────────────────────┘
    │
    ▼  (if TWELVELABS_ENABLED)
┌─────────────────────────────────────────────────────────────────────────┐
│ 3. TL INDEX        job: twelvelabs_index                                │
│    ffmpeg splits VOD if &gt; TWELVELABS_MAX_UPLOAD_BYTES (~1.9 GB)        │
│    TwelveLabs v1.3: multipart asset upload → index-content               │
│    APIs: POST /assets, /assets/multipart-uploads, /indexes/…/indexed-assets │
│    Out: external_video_indexes rows (per chunk)                          │
└─────────────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 4. TL VISUAL       job: twelvelabs_analyze                                │
│    Pegasus 1.5 — POST /analyze (sync) or /analyze/tasks (async, long)   │
│    Marengo 3.0 — POST /search (multipart, visual+audio)                   │
│    Out: visual_segments table                                            │
└─────────────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 5. ANALYZE         job: analyze                                           │
│    PySceneDetect — scene cuts (local)                                     │
│    librosa — RMS + onset excitement curve                                 │
│    Optional — Twitch chat density from ingested chat file                 │
│    Candidates — sliding windows over transcript + audio/chat/TL visual    │
│    Gemini 3.5 Flash / 3.1 Pro — rerank top candidates (google-genai)    │
│    Optional — Gemini multimodal boundary refine (env flag)                │
│    Out: highlights (top N per project settings)                           │
│    Project status → ready                                                 │
└─────────────────────────────────────────────────────────────────────────┘
    │
    ▼  (user-driven)
┌─────────────────────────────────────────────────────────────────────────┐
│ 6. RENDER          job: render — FFmpeg scene-snap, aspect crop/blur fill │
│ 7. CAPTION         job: caption — libass ASS burn-in                      │
│ 8. REEDIT          job: reedit — trim/caption overrides from clip editor  │
│ 9. PUBLISH         job: publish — YouTube Data API / Instagram Graph API  │
└─────────────────────────────────────────────────────────────────────────┘
```

### Stack summary

| Layer | Tech |
|-------|------|
| **UI** | Next.js 15 App Router, React, Tailwind, shadcn/ui |
| **Web DB** | Drizzle ORM → SQLite (`data/app.db`) |
| **Worker** | Python 3.11, FastAPI, SQLAlchemy, structlog |
| **Media** | yt-dlp, FFmpeg, librosa, PySceneDetect, Pillow |
| **Transcription** | faster-whisper or Groq Whisper |
| **Video AI** | TwelveLabs v1.3 (Pegasus 1.5, Marengo 3.0) — optional |
| **Text AI** | Google Gemini API (`google-genai`) — rerank + metadata |
| **Captions** | libass via FFmpeg (`packages/remotion` for future) |
| **Progress** | SSE `GET /api/projects/:id/events` |

---

## Quick start

```powershell
pnpm install
pnpm --filter worker run setup
pnpm --filter worker run setup:ingest
# Optional: pnpm --filter worker run setup:transcribe
# Optional: pnpm --filter worker run setup:analyze

Copy-Item .env.example .env
# Set GEMINI_API_KEY (required for LLM rerank)
# Set TWELVELABS_* if using visual analysis

pnpm db:migrate
pnpm dev
```

- App: <http://localhost:3000>
- Worker health: <http://127.0.0.1:8000/health>

Paste a URL on **New project**, open the project page for live pipeline progress.

---

## Environment (minimum)

| Variable | Purpose |
|----------|---------|
| `GEMINI_API_KEY` | Highlight rerank (core) |
| `GEMINI_FLASH_MODEL` / `GEMINI_PRO_MODEL` | Default `gemini-3.5-flash` / `gemini-3.1-pro-preview` |
| `TWELVELABS_ENABLED` + `TWELVELABS_API_KEY` + `TWELVELABS_INDEX_ID` | Optional visual pipeline |
| `GROQ_API_KEY` + `TRANSCRIBE_BACKEND=groq` | Fast hosted transcription |
| `YOUTUBE_*` / `INSTAGRAM_*` | Publishing only |

Full list: `.env.example`.

---

## Prerequisites

| Tool | Role |
|------|------|
| Node 22+, pnpm 11+ | Web app |
| Python 3.11 | Worker venv (`apps/worker/.venv`) |
| yt-dlp, ffmpeg, ffprobe | Ingest (on PATH) |
| NVIDIA + CUDA (optional) | GPU Whisper |

Windows:

```powershell
winget install yt-dlp.yt-dlp
winget install Gyan.FFmpeg
```

---

## Project layout

```
AiClipper/
├── apps/web/           # Next.js UI + API routes
├── apps/worker/        # Python worker (jobs/, analyze/, providers/)
├── packages/remotion/  # Future rich captions
├── data/app.db         # SQLite (gitignored)
└── data/videos/        # Per-project media (gitignored)
```

---

## Verify (smoke scripts)

```powershell
cd apps\worker
.venv\Scripts\python scripts\smoke_ingest.py
.venv\Scripts\python scripts\smoke_transcribe.py
.venv\Scripts\python scripts\smoke_analyze.py
.venv\Scripts\python scripts\smoke_twelvelabs.py   # if TL enabled
.venv\Scripts\python scripts\smoke_render.py
```

---

## User-facing features (detail)

**Project settings** (`/projects/new` → Advanced): `topN`, min/max clip seconds, aspect (9:16 / 16:9 / 1:1), vibe text, analyze tier (`flash` | `pro`).

**Highlight signals:** transcript windows, audio peaks, chat density (when available), TwelveLabs visual segments, Gemini rerank with per-signal `reasonJson`.

**Render:** PySceneDetect snap ±1.5 s, blurred-background 9:16 letterbox, loudnorm, dominant-color for caption styling.

**Captions:** Presets (highlight, popup, karaoke, minimal), fonts, auto-contrast colors from frame.

**Uploads:** Schedule YouTube + Instagram; default timezone `America/Chicago`; worker scheduler tick every 15 s.

**Admin:** `/admin` — heal stuck jobs, delete failed projects, prune job history. `pnpm worker:reset` if port 8000 is wedged.

---

## Architecture

```
┌─────────────────────────┐         ┌──────────────────────────┐
│  apps/web  (Next.js)    │ ──HTTP─▶│  apps/worker  (FastAPI)  │
│  Drizzle → SQLite       │◀──SSE───│  Job runner (poll SQLite)│
│  :3000                  │         │  :8000                   │
└───────────┬─────────────┘         └────────────┬─────────────┘
            │                                    │
            └──────────── data/app.db ───────────┘
                         data/videos/
```

Both processes share one SQLite file. Jobs are rows in `jobs`; the worker claims them atomically (`job_max_concurrent` default 1).

---

## Cost ballpark (5 h VOD, local GPU transcribe)

| Stage | Cost |
|-------|------|
| Download, render, captions | $0 (local) |
| Groq transcribe (optional) | ~$0.20 |
| Gemini rerank | ~$0.05–0.15 |
| TwelveLabs (if enabled) | Per TwelveLabs pricing |
| **Typical without TL** | **~$0.10–0.30** |

---

## Build checklist (reference)

- [x] Workspace, schema, worker job loop
- [x] Ingest, transcribe, analyze, render, caption, publish
- [x] TwelveLabs index + visual (v1.3, chunked upload)
- [x] Gemini 3.x tiers, analysis dashboard, clip editor
- [x] SSE live progress, project delete, OAuth uploads
- [ ] Thumbnails (skipped)
- [ ] Pegasus long-chunk async / timeout hardening
- [ ] Marengo hit quality tuning
