# AiClipper

A personal-use web app that ingests long-form videos (Twitch / YouTube VODs,
local files) and uses AI to extract short highlight clips, generate captions
and thumbnails, and schedule uploads to YouTube and TikTok.

Built to be **cheap to run** (mostly local, no managed cloud infra) and
**accurate** (multi-signal highlight detection: chat density, audio energy,
LLM transcript ranking).

## Architecture

```
┌─────────────────────────────┐         ┌────────────────────────────┐
│  apps/web  (Next.js 15)     │ ──HTTP─▶│  apps/worker  (Python)     │
│  - UI, project management   │         │  - yt-dlp, ffmpeg          │
│  - Drizzle ORM → SQLite     │◀──SSE───│  - faster-whisper (GPU)    │
│  - shadcn/ui                │         │  - Gemini Flash, librosa   │
│  port 3000                  │         │  port 8000                 │
└──────────────┬──────────────┘         └─────────────┬──────────────┘
               │                                      │
               ▼                                      ▼
  ┌──────────────────────────────────────────────────────────────┐
  │  data/app.db  (SQLite, shared)   data/videos/  (media files) │
  └──────────────────────────────────────────────────────────────┘
                                │
                                ▼
                ┌─────────────────────────────────┐
                │  External APIs                  │
                │  • Google Gemini (highlights)   │
                │  • YouTube Data API (upload)    │
                │  • TikTok Content API (upload)  │
                └─────────────────────────────────┘
```

## Project structure

```
AiClipper/
├── apps/
│   ├── web/          # Next.js 15 frontend + thin API layer
│   └── worker/       # Python FastAPI: AI + video processing
├── packages/
│   └── remotion/     # Caption-overlay video templates
├── data/
│   ├── app.db        # SQLite (gitignored)
│   └── videos/       # Source videos, clips, thumbnails (gitignored)
├── scripts/          # One-off utility scripts
├── .env              # Local secrets (gitignored)
└── .env.example      # Template documenting all env vars
```

## Quick start

```powershell
# From the repo root
pnpm install

# Python worker venv + ingest extras (yt-dlp Python package)
pnpm --filter worker run setup
pnpm --filter worker run setup:ingest

# System tools for ingest (must be on PATH — see Setup below)
#   yt-dlp, ffmpeg, ffprobe

Copy-Item .env.example .env
pnpm db:migrate
pnpm dev
```

Open <http://localhost:3000>, click **New project**, paste a YouTube or Twitch VOD URL, and watch the project page while the worker downloads the video.

Worker health check: <http://127.0.0.1:8000/health> — expect `"ingest": true` in `capabilities` and `"ingest"` in `registered_handlers`.

## Setup

### Prerequisites

| Tool | Used for |
|------|----------|
| Node.js 22+ (LTS) | Next.js, pnpm |
| pnpm 11+ | Monorepo scripts |
| Python 3.11 | Worker |
| **yt-dlp** | Download source video (Step 5) |
| **ffmpeg** + **ffprobe** | Merge/probe video, extract audio (Step 5+) |
| Git | Version control |
| NVIDIA GPU + drivers (optional) | Local Whisper in Step 6 |

Install **yt-dlp** and **FFmpeg** on Windows (pick one):

```powershell
winget install yt-dlp.yt-dlp
winget install Gyan.FFmpeg
```

After install, ensure their `bin` folders are on your user **PATH**, then open a **new** terminal and verify:

```powershell
yt-dlp --version
ffmpeg -version
ffprobe -version
```

### Windows notes

- **pnpm**: If `pnpm` is not recognized, add `C:\Program Files\nodejs` and `%APPDATA%\npm` to your user PATH.
- **PowerShell scripts**: If `pnpm` fails with an execution-policy error, run once:  
  `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`
- **Python**: Use `pnpm --filter worker run setup` (not bare `python`) so the venv is created via the `py` launcher. The worker `setup` script uses `py -3 -m venv`.
- **Ports**: Stop any old `pnpm dev` with `Ctrl+C` before restarting. If port 3000 or 8000 is stuck, kill orphaned `node` / `python` processes from a previous dev session.

### Required API keys

| Service | Where to get it | Required for |
|---|---|---|
| Google Gemini | <https://aistudio.google.com/apikey> | Highlight detection (core) |
| Groq (optional) | <https://console.groq.com/keys> | Whisper API fallback |
| YouTube OAuth | <https://console.cloud.google.com/apis/credentials> | Scheduled uploads |
| TikTok OAuth | <https://developers.tiktok.com/> | Scheduled uploads |

## Build progress

- [x] Step 1 — Prerequisites + workspace bootstrap
- [x] Step 2 — Next.js scaffold (Tailwind, shadcn/ui, project list)
- [x] Step 3 — Drizzle schema + SQLite migration (11 tables, FK cascades)
- [x] Step 4 — Python FastAPI worker (job loop, SQLAlchemy mirror, health endpoint)
- [x] Step 5 — End-to-end ingest pipeline (yt-dlp download)
- [ ] Step 6 — Transcription stage (faster-whisper local GPU)

### Step 5 — What works today

1. **Web**: `/projects/new` — paste a YouTube or Twitch URL → creates a `projects` row and enqueues an `ingest` job on the worker.
2. **Worker**: Downloads with yt-dlp to `data/videos/{project_id}/source.mp4`, probes metadata with ffprobe, extracts `audio.wav` (16 kHz mono for transcription), writes a `videos` row.
3. **Web**: `/projects/{id}` — live job progress (page refresh while running) and source file metadata when complete.

Ingest outputs per project:

```
data/videos/{project_id}/
  source.mp4    # merged source video
  audio.wav     # mono 16 kHz for Step 6
```

### Verify Step 5

```powershell
# Handler registered (no network)
cd apps\worker
.venv\Scripts\python scripts\smoke_ingest.py

# Full stack
pnpm dev
# → http://localhost:3000/projects/new → short public YouTube URL
# → confirm files under data/videos/{id}/ and job status succeeded in UI or:
pnpm db:studio
```
- [ ] Step 7 — Highlight analysis (audio + chat + Gemini Flash)
- [ ] Step 8 — Clip rendering (FFmpeg, scene snapping, vertical reformat)
- [ ] Step 9 — Captions (Remotion overlay)
- [ ] Step 10 — Thumbnails (frame extraction + Satori)
- [ ] Step 11 — Live progress UI (Server-Sent Events)
- [ ] Step 12 — YouTube OAuth + scheduled uploads
- [ ] Step 13 — TikTok upload integration

## Cost estimate (per 5-hour VOD)

| Stage | Tool | Cost |
|---|---|---|
| Download | yt-dlp (local) | $0 |
| Transcribe | faster-whisper on GPU (local) | $0 |
| Highlight ranking | Gemini 2.5 Flash | ~$0.10 |
| Clip render | FFmpeg (local) | $0 |
| Captions | Remotion (local) | $0 |
| Thumbnails | Frame extract + Satori (local) | $0 |
| Upload | YouTube / TikTok APIs | $0 |
| **Total** | | **~$0.10** |
