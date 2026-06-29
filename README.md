# Resume-Driven Job Matching

> README skeleton — filled in by the M_final polish phase. See `PLAN.md` for the
> mission and `SPEC.md` for the contract.

A web app that takes your resume, finds jobs via Adzuna, extracts each job's full
description, and shows you **only** the jobs where the description was actually
extracted and your resume meets the stated requirements (with cited evidence and a
strict confidence threshold). Orchestrated with LangGraph; semantic work done by
low-cost open-weight models via OpenRouter (GLM 5.2 for logic, Gemini 3.1 Flash Lite
for text extraction).

## Architecture (one line)

`Next.js (browser)` → `FastAPI` → `LangGraph pipeline` (resume → profile cache →
Adzuna search → per-job: resolve → fetch → extract JD → parse requirements → judge
fit) → qualified jobs. See `docs/system-overview.html`.

## Prerequisites

- [**uv**](https://docs.astral.sh/uv/) — Python package manager (fetches Python 3.11 for you)
- [**bun**](https://bun.com/) — JS package manager + runtime (no separate Node install)
- An OpenRouter API key, and Adzuna `app_id` + `app_key`

## Setup

```bash
# API (uv reads pyproject.toml + uv.lock, creates .venv with Python 3.11)
cd packages/api
uv sync
uv run playwright install chromium    # REQUIRED — JS-render fallback needs it
# (Linux only) uv run playwright install-deps
cp ../../.env.example .env             # -> packages/api/.env (config.py loads THIS path); fill in keys

# Web (bun reads package.json + bun.lock)
cd ../web
bun install
echo 'NEXT_PUBLIC_API_BASE=http://localhost:8000' > .env.local
```

## Run

```bash
# terminal 1 — API
cd packages/api
uv run uvicorn jdparser.server:app --port 8000

# terminal 2 — web
cd packages/web && bun run dev      # http://localhost:3000
```

CLI (dev / verification):
```bash
cd packages/api && uv run python -m jdparser path/to/resume.pdf
```

### Docker

The whole stack runs in containers (API image bundles Playwright + chromium; web
image builds the Next app). You still need `packages/api/.env` with your keys.

```bash
cp .env.example packages/api/.env     # fill in OPENROUTER_API_KEY / ADZUNA_APP_ID / ADZUNA_APP_KEY
docker compose up --build             # builds both images, starts api:8000 + web:3000
# open http://localhost:3000
```

Notes:
- `packages/api/.env` is passed to the API container via compose `env_file` (it is
  never baked into the image). After changing a key, recreate the container so it is
  re-read: `docker compose up -d --force-recreate api`.
- `NEXT_PUBLIC_API_BASE` is inlined into the browser bundle at **build** time
  (default `http://localhost:8000`); change the compose `build.args` and rebuild the
  web image if the browser must reach the API at a different host/port.
- The resume cache + run records persist in the named volume `jdparser-data`.
- Tear down with `docker compose down` (add `-v` to also drop the data volume).

## Definition of done (how to confirm it works)

1. Open http://localhost:3000 and upload a resume (PDF/DOCX/TXT).
2. Wait for the run to complete (a run takes ~2–3 min: a fan-out of live job pages
   are fetched and judged by an LLM; the browser polls every 2s up to a 5-min
   ceiling). You should see ≥1 **qualified** job card with the company, title, a link
   to the real job URL, and resume evidence cited for each met requirement.
3. Upload the **same** resume again — the run reports "loaded your profile from
   cache" (`resume_cache_hit = true`), skipping the profiler LLM call.
4. Jobs whose description couldn't be extracted, or that the judge marked uncertain
   or below the 0.75 confidence threshold, appear only in the **audit/failures**
   panel — never the main feed.

## Tuning the LLM nodes

Per-node model, temperature, `max_tokens`, and reasoning are env-overridable (defaults
are generous; see SPEC §6.3). Override any of `LLM_{MODEL,TEMP,MAX_TOKENS,REASONING}_<NODE>`
where `<NODE>` ∈ `PROFILER, PLANNER, JD_PARSER, JUDGE`, e.g. `LLM_MAX_TOKENS_JUDGE=14000`
or `LLM_REASONING_JUDGE=medium`. `max_tokens` is a ceiling billed only as generated, so
raising it costs nothing unless used.

## Gotchas

- **Playwright:** `uv run playwright install chromium` must run after `uv sync`, or
  JS-rendered job pages fail to fetch.
- **Tailwind v4** (web) is CSS-first: `@import "tailwindcss";` in `app/globals.css`,
  no `tailwind.config.js`.

## MVP limitations (intentional — see SPEC §9)

Single local user (no auth); Adzuna `us` only; JSON flat-file storage (no DB);
results update by **polling** (no live stream); in-memory graph checkpointer.
