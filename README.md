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

> First-time setup: see [SETUP.md](#setup) below.

```powershell
# install JS deps
pnpm install

# create Python venv and install deps (one-time)
cd apps/worker
python -m venv .venv
.venv\Scripts\pip install -e .
cd ../..

# create your .env from the template, then edit
Copy-Item .env.example .env

# initialize the database
pnpm db:migrate

# run both services in one terminal
pnpm dev
```

Open <http://localhost:3000> in your browser.

## Setup

### Prerequisites (already installed during bootstrap)

- Node.js 24 LTS
- Python 3.11
- FFmpeg 8.x
- yt-dlp
- Git
- pnpm 11+
- NVIDIA GPU with current drivers (for local Whisper)

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
- [ ] Step 5 — End-to-end ingest pipeline (yt-dlp download)
- [ ] Step 6 — Transcription stage (faster-whisper local GPU)
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
