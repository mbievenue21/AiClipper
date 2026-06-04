# AiClipper

A personal-use web app that ingests long-form videos (Twitch / YouTube VODs,
local files) and uses AI to extract short highlight clips, generate captions
and thumbnails, and schedule uploads to YouTube and Instagram Reels.

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
                │  • Instagram Graph API (Reels)  │
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
| Instagram (Meta) | <https://developers.facebook.com/apps/> | Reels uploads |

## Build progress

- [x] Step 1 — Prerequisites + workspace bootstrap
- [x] Step 2 — Next.js scaffold (Tailwind, shadcn/ui, project list)
- [x] Step 3 — Drizzle schema + SQLite migration (11 tables, FK cascades)
- [x] Step 4 — Python FastAPI worker (job loop, SQLAlchemy mirror, health endpoint)
- [x] Step 5 — End-to-end ingest pipeline (yt-dlp download)
- [x] Step 6 — Transcription stage (faster-whisper local GPU)

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

### Step 6 — What works today

After ingest finishes, the worker now auto-enqueues a **transcribe** job that
runs faster-whisper against `audio.wav` and writes the result to the
`transcripts` + `transcript_segments` tables.

- **Backend selection:** auto-picks CUDA if available, otherwise CPU + int8.
- **Word timestamps:** stored as JSON on each segment for karaoke captions (Step 9) and snap-to-word clip cutting (Step 8).
- **VAD filter:** silence is skipped, so long VODs transcribe faster.
- **Model cache:** the loaded WhisperModel is reused across jobs in the same worker process (load ≈ 30 s for `large-v3`).
- **Project status:** `pending → ingesting → pending → transcribing → analyzing → ready` (the `analyzing` stage was added in Step 7).

What lands in the DB after a successful transcribe:

```text
transcripts        1 row per video  (language, model, full_text)
transcript_segments  N rows         (start_seconds, end_seconds, text, words_json)
```

### Install Step 6

```powershell
pnpm --filter worker run setup:transcribe   # installs faster-whisper + ctranslate2
```

**GPU notes (Windows):** faster-whisper needs cuBLAS and cuDNN at runtime.
If you installed the CUDA Toolkit those DLLs are already on PATH. If you
don't have them, the worker falls back to CPU + int8 automatically — slower
but still works. To force CPU explicitly, set `WHISPER_COMPUTE_TYPE=int8`
in `.env`.

**First run downloads model weights** to `~/.cache/huggingface/hub/` (~3 GB
for `large-v3`, ~470 MB for `small`). Override `WHISPER_MODEL=small` in `.env`
for much faster initial setup with very usable quality.

### Verify Step 6

```powershell
# Handler registered + faster-whisper imports + device detection (no model load)
cd apps\worker
.venv\Scripts\python scripts\smoke_transcribe.py
```

End-to-end: submit a short YouTube URL in the UI and watch the project page.
You should see the **Ingest** job complete, then a **Transcribe** job appear
and progress to 100 %, and finally a **Transcript** card with the language,
segment count, and a preview of the full text.

### Step 7 — What works today

After transcription, the worker auto-enqueues an **analyze** job that turns
the transcript + audio + (optional) chat into a ranked list of highlight
clips:

1. **`librosa`** computes a per-second "excitement" curve from RMS energy +
   onset strength. Saved to the `audio_features` table.
2. **Twitch chat replay** (if `ingest` captured one) is parsed into
   `chat_events` rows and binned into a per-second density curve.
3. A **sliding-window scorer** walks the transcript and produces candidate
   clips that respect the user's min/max length, blending audio + chat +
   keyword signals and applying non-max suppression.
4. **Gemini 2.5 Flash** reranks the top ~15 candidates against the user's
   "vibe" hint and writes punchy titles + 1–2 sentence summaries.
   If `GEMINI_API_KEY` is missing, the pipeline degrades gracefully to
   local scoring with auto-generated titles.
5. The final top-N highlights land in the `highlights` table with
   `score`, `title`, `summary`, and a `reasonJson` blob that records every
   signal that contributed.

#### User-configurable settings (per project)

Set on the **/projects/new** form under "Advanced — clip settings" and
stored as `projects.settings_json`:

| Setting           | Default | Range         | Purpose                                  |
|-------------------|---------|---------------|------------------------------------------|
| `topN`            | 3       | 1–20          | How many highlights to keep              |
| `minClipSeconds`  | 20      | 5–120         | Lower bound on clip duration             |
| `maxClipSeconds`  | 60      | 10–180        | Upper bound on clip duration             |
| `aspect`          | 9:16    | 9:16 \| 16:9 \| 1:1 | Output aspect ratio for Step 8       |
| `vibe`            | ""      | free text     | Steers Gemini's selection ("funny", etc.)|

### Install Step 7

```powershell
pnpm --filter worker run setup:analyze   # librosa, scenedetect, google-genai
```

Then in `.env`, set **`GEMINI_API_KEY`** to your key from
[Google AI Studio](https://aistudio.google.com/apikey). The free tier is
generous and Gemini 2.5 Flash is cheap; without a key the pipeline still
works, it just uses local-only scoring.

If you change the schema (already done for `settings_json` in Step 7) you
must also run the migration:

```powershell
pnpm --filter web run db:generate   # generates lib/db/migrations/NNNN_*.sql
pnpm --filter web run db:migrate    # applies pending migrations
```

### Verify Step 7

```powershell
# Handler registered + librosa/scenedetect/google-genai imports + API key check
cd apps\worker
.venv\Scripts\python scripts\smoke_analyze.py
```

End-to-end: create a new project. After ingest and transcribe finish,
watch the **Analyze** job card climb to 100 %, then a **Highlight
candidates** card appears showing the top-N clips with scores, titles,
summaries, and per-signal breakdown chips.
- [x] Step 7 — Highlight analysis (audio + chat + Gemini Flash)
- [x] Step 8 — Clip rendering (FFmpeg, scene snapping, vertical reformat with blurred fill)
- [x] Step 9 — Captions (ASS subtitle overlays; Remotion package available for future expansion)
- [ ] Step 10 — Thumbnails (frame extraction + Satori) — _intentionally skipped for now_
- [x] Step 11 — Live progress UI (Server-Sent Events)
- [x] Step 12 — YouTube OAuth + scheduled uploads
- [x] Step 13 — Instagram Reels publishing (replaces the planned TikTok step)

### Step 8 — What works today (clip rendering)

The user clicks **Render clip** on any highlight; the worker enqueues a
`render` job that:

1. **Scene-snaps** the start/end to the nearest content cut within ±1.5 s
   using PySceneDetect — cleaner ins and outs than raw AI timestamps.
2. **Cuts + reformats** with ffmpeg. For vertical output from a landscape
   source we use the industry-standard *blurred-background fill*: a
   scaled, boxblurred copy of the same frame sits behind the centered
   original. No black bars.
3. **Normalizes audio** with `loudnorm=I=-16:TP=-1.5:LRA=11` so a series
   of clips don't jump in volume between cuts.
4. **Extracts a dominant color** from the middle frame using Pillow's
   k-means quantizer. Saved to `clips.dominant_color` and used in Step 9
   to gradient captions toward a contrasting hue.
5. Writes the final file to
   `data/videos/<project>/clips/<clip_id>/clip.mp4` and updates the
   `clips` row to `status = "ready"`.

Default output sizes: **1080×1920 (9:16)**, 1080×1080 (1:1), 1920×1080
(16:9). All clips are h.264 + AAC + faststart, ready for direct upload to
any platform.

### Step 9 — What works today (captions)

The Render dialog has a **Burn captions immediately** toggle (on by default)
and a full style picker:

- **Style preset** (4 options):
  - `highlight` — current word in primary color, full line in accent
  - `popup`     — each word springs in with a tiny scale animation
  - `karaoke`   — line stays on screen, sweeps through words (libass `\k`)
  - `minimal`   — clean static block, no per-word animation
- **Font** (6 options): Anton, Bebas Neue, Inter, Montserrat, Permanent
  Marker, Roboto Mono.
- **Auto-color** (default ON): caption gradient is derived from the clip's
  dominant frame color — picks a high-contrast `(primary, accent)` pair
  using a luminance heuristic. Disabling auto-color reveals two color
  pickers for manual control.
- **UPPERCASE** toggle.

Captions are baked into a sibling `clip-captioned.mp4` via ffmpeg's
`subtitles` filter (libass). Rendering is ~2–5 s per clip on a typical
laptop. The `packages/remotion/` package contains a parallel React-based
implementation that can be swapped in for richer animations later — see
its README for the swap point.

After a clip is rendered the **Rendered clips** card shows it with an
inline `<video>` preview, **Restyle captions** / **Schedule upload** /
**Download** / **Delete** actions, and a thumbnail of the dominant color.

### Step 11 — What works today (live progress / SSE)

- `GET /api/projects/<id>/events` opens a Server-Sent Events stream.
- The route polls the DB every 750 ms and ONLY pushes a new event when the
  digest of the project snapshot changes (idle projects → near-zero
  traffic).
- Each `snapshot` event triggers `router.refresh()` on the client, so the
  UI updates within ~1 second of any state change (job progress, clip
  status, upload status) without a full reload.
- Keepalive comments every 20 s keep proxies/Next dev server happy.
- 15-minute cap on a single stream lifetime so abandoned tabs eventually
  release the connection.

### Step 12 + 13 — What works today (scheduled uploads)

Each rendered clip has a **Schedule upload** dialog that lets the user:

- Pick **one or both** of YouTube + Instagram (post to both simultaneously).
- Choose **Post now** or **Schedule for** a wall-clock time + IANA
  timezone. **Default timezone is `America/Chicago` (Central)** per spec.
- Provide title, description (multi-line), tags (comma-separated).
- Choose visibility: **Private** (default), Unlisted, Public.
  - YouTube respects this; Instagram Reels are always public.

Submitting writes a `scheduled_uploads` row. If the chosen time is "now"
the publish job is enqueued immediately; otherwise the worker's
**scheduler tick** (runs every 15 s as part of the job loop) picks up
due rows and enqueues publish jobs at the right moment.

The Python `publish` job:

- Reads tokens from the `accounts` table (manage on `/accounts`).
- For YouTube: refreshes the access token via OAuth2 if the API returns
  401 and `YOUTUBE_CLIENT_ID` / `_SECRET` are set; otherwise raises
  `AuthExpiredError` and the upload is marked failed (non-retryable) so
  the user reconnects.
- For Instagram: builds a public HTTPS URL from `NEXT_PUBLIC_APP_URL` +
  `/api/media/<clip>` and runs the three-step Reels flow
  (`POST /me/media` container → poll `status_code` until `FINISHED` →
  `POST /me/media_publish`). The long-lived token (60 days) is
  auto-refreshed on 401 via `/refresh_access_token`. Captions become the
  IG caption with the user's tags appended as hashtags.
- Uploads as **private by default** on platforms that support it.

OAuth setup
-----------
Click **Connect with YouTube** or **Connect with Instagram** on
`/accounts`. The full OAuth flows are wired end-to-end —
`/api/auth/{youtube,instagram}/start` redirects to the provider,
`/callback` exchanges the code for tokens, fetches a human-readable
label (channel name / `@username`), and upserts the account row. See
`.env.example` for the developer-console setup steps.

For Instagram specifically: the account **must** be Business or Creator,
must be added as a tester in the Meta app dashboard, and your
`NEXT_PUBLIC_APP_URL` must be a public HTTPS URL (use a Cloudflared or
ngrok tunnel for local dev) — Instagram fetches the video from that URL.

### Worker tools (`/admin`, `pnpm worker:reset`)

Visit `/admin` for a live worker-health dashboard with one-click cleanup
buttons (heal stuck workers, cancel pending jobs, prune finished jobs,
delete failed projects). When the worker itself is unresponsive, run
`pnpm worker:reset` in a terminal — it kills any zombie uvicorn
processes, frees port 8000, and resets stuck DB state.

### Verify Step 8 + 9 + 11 + 12

```powershell
# Worker-side smoke checks (no model loading, no rendering).
cd apps\worker
.venv\Scripts\python scripts\smoke_render.py    # render + caption + publish

# Web-side typecheck (no asChild errors expected).
cd ..\..\apps\web
npx tsc --noEmit
```

End-to-end: create a project, wait for it to reach **highlights ready**,
click **Render clip** on any highlight (turn on captions, pick a style),
wait for the **Rendered clips** card to show the preview, then click
**Schedule upload** to either post now or queue for a future Central-time
slot.

## Cost estimate (per 5-hour VOD)

| Stage | Tool | Cost |
|---|---|---|
| Download | yt-dlp (local) | $0 |
| Transcribe | faster-whisper on GPU (local) | $0 |
| Highlight ranking | Gemini 2.5 Flash | ~$0.10 |
| Clip render | FFmpeg (local) | $0 |
| Captions | Remotion (local) | $0 |
| Thumbnails | Frame extract + Satori (local) | $0 |
| Upload | YouTube / Instagram APIs | $0 |
| **Total** | | **~$0.10** |
