# Lectov

Turns uploaded lecture notes (`.txt` or `.pdf`) into short Manim animations.

Teachers and students can generate educational animations directly from class notes. The resulting video is pure Python via [Manim](https://www.manim.community/), so any generated scene can be tweaked deterministically.

---

## How it works

```
Upload → Planner → Writer → Syntax check → Render check → Repair loop → MP4
```

Three LLM agents, each with its own model and fallback list:

| Agent | Role |
|---|---|
| **Planner** | Reads the lecture, outputs a structured JSON brief (sections, formulas, timings) |
| **Writer** | Turns the brief into runnable Manim Python |
| **Repair** | Fixes syntax or render errors — up to 3 retries per stage |

Jobs run asynchronously via Celery + Redis. The Flask API returns a `job_id` immediately; you poll `/status/<job_id>` until it's done.

---

## Setup

**Prerequisites:** Python 3.13+, Redis, LaTeX (`texlive`), `uv`

```bash
# 1. Clone and install
git clone <repo>
cd lectov
uv sync

# 2. Configure
cp .env.example .env
# Fill in your keys:
#   OPENROUTER_API_KEY=sk-or-v1-...
#   GEMINI_API_KEY=...   (optional fallback)
```

**Get a free OpenRouter key at** [openrouter.ai](https://openrouter.ai) — no billing required for free-tier models.

---

## Running

Start everything with one command:

```bash
uv run honcho start
```

This launches Redis, the Celery worker, and Flask together with colour-coded logs. Redis must not already be running on port 6379 — if it is, stop the existing instance first or remove the `redis:` line from `Procfile`.

---

## Generating a video

**Upload and watch in one command:**

```bash
uv run watch.py --upload lecture.txt
```

Output while running:
```
Queued: {"job_id": "abc-123", "status": "queued"}

Watching job abc-123 (checking every 10s) ...

  running  [planning]  planner=gpt-oss-120b:free
  running  [generating]  writer=deepseek-chat:free  planner=gpt-oss-120b:free
  running  [render_check]  writer=deepseek-chat:free  planner=gpt-oss-120b:free
  success

Download: curl http://localhost:6001/video/abc-123.mp4 -o output.mp4
```

**Or use curl directly:**

```bash
# Upload
curl -X POST http://localhost:6001/generate -F "file=@lecture.txt"
# → {"job_id": "abc-123", "status": "queued"}

# Poll status
curl http://localhost:6001/status/abc-123

# Download when successful
curl http://localhost:6001/video/abc-123.mp4 -o output.mp4

# Watch an existing job
uv run watch.py abc-123
```

Accepted formats: `.txt`, `.pdf` (max 10 MB).

---

## Configuration

All settings are in `.env`. Key variables:

| Variable | Default | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | required | OpenRouter API key |
| `LITELLM_MODEL` | `openrouter/deepseek/deepseek-chat:free` | Primary writer model |
| `LITELLM_MODEL_FALLBACKS` | comma-separated list | Writer fallback models |
| `PLANNER_MODEL` | `openrouter/openai/gpt-oss-120b:free` | Primary planner model |
| `PLANNER_MODEL_FALLBACKS` | comma-separated list | Planner fallback models |
| `REDIS_URL` | `redis://localhost:6379/0` | Celery broker |
| `MAX_UPLOAD_BYTES` | `10485760` (10 MB) | Upload size cap |
| `LLM_TIMEOUT` | `60` | Seconds before LLM call times out |

---

## Architecture

```
config.py       Pydantic Settings — single source of all env vars
celery_app.py   Celery + Redis config
tasks.py        Pipeline logic: planner, writer, repair agents + Celery task
main.py         Flask HTTP layer: /generate, /status/<id>, /video/<file>
watch.py        CLI helper: upload + poll status every 10 seconds
Procfile        honcho process definitions
```

---

## Roadmap

- [ ] Handwritten image support
- [ ] Web UI (upload form + live status polling)
- [ ] Per-job Docker sandboxing for generated code execution
- [ ] Object storage (S3/GCS) for multi-worker deployments
