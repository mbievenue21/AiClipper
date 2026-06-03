# AiClipper Worker

Python FastAPI service responsible for the heavy lifting:
download → transcribe → analyze → render → publish.

Reads/writes the same SQLite file as the Next.js app (`../../data/app.db`).

## Setup

```powershell
# from the repo root
pnpm --filter worker setup
```

This creates `.venv` and installs the core dependencies. Heavy ML/video
deps are split into extras (`[transcribe]`, `[analyze]`, `[render]`, …)
and only installed when their pipeline step is enabled — see the
`setup:*` scripts in `package.json`.

## Running

```powershell
pnpm --filter worker dev   # uvicorn with --reload
```

Then: <http://127.0.0.1:8000/health>
