# IMPLEMENTATION — overview & conventions

> Table of contents + shared style guide. Detail lives in `IMPLEMENTATION_<AREA>.md`.
> Phases and exit-checks live in `BUILD.md`. Contracts live in `SPEC.md`.

> **The code is authoritative.** These docs quote implementation inline to explain intent,
> and quoted snippets drift — several here described `file_hash`, `text_hash`, and
> `resume_profile_id` for months after those fields were deleted. When a snippet and the
> source disagree, the source wins; fix the snippet. Prefer explaining WHY a thing is
> shaped the way it is over reproducing WHAT it says, which the reader can just go read.

## Milestones

| Milestone | What | Detail doc |
|---|---|---|
| **M0 — Foundations** | Monorepo scaffold (`packages/api`, `packages/web`), pinned deps installed, Playwright browser installed, `.env.example`, empty `data/` dirs, `config.py` with all SPEC §6 constants. | this doc (§Foundations) |
| **M1 — Cache & resume text** | `resume/extract_text.py`, `cache/fingerprint.py`, `cache/store.py`. Pure logic, unit-tested. | `IMPLEMENTATION_CACHE.md` |
| **M2 — LLM agents** | `llm/schemas.py` (all Pydantic models), `llm/client.py` (routing), the 6 agent fns. | `IMPLEMENTATION_LLM.md` |
| **M3 — Adzuna** | `jobsource/adzuna/client.py`, `jobsource/dedupe.py`. | `IMPLEMENTATION_ADZUNA.md` |
| **M4 — Extraction** | `extract/*` (resolve, fetch+Playwright, jsonld, ats, readable, quality). | `IMPLEMENTATION_EXTRACT.md` |
| **M5 — Graph** | `graph/state.py`, `graph/nodes.py`, `graph/subgraph.py`, `graph/build.py`; `__main__.py` CLI. | `IMPLEMENTATION_GRAPH.md` |
| **M6 — API** | `runs/store.py`, `server.py`, background execution. | `IMPLEMENTATION_API.md` |
| **M7 — Web** | Next.js upload → poll → results + audit. | `IMPLEMENTATION_WEB.md` |
| **M_final — Polish** | README, end-to-end browser check, `.env` docs, audit-view copy discloses MVP stubs (SPEC §9). | `BUILD.md` (final phase) |

M2–M4 are independent (no shared files) and may run in parallel after M1. M5
depends on M2–M4. M6 depends on M5. M7 depends on M6.

## Cross-cutting conventions

- **Language/runtime.** Backend: Python **3.11** (LangGraph needs `TypedDict` +
  `operator.add`; `str | None` syntax requires ≥3.10). Frontend: TypeScript 6 on
  Next.js 16 (App Router).
- **Package managers.** Python → **uv** (`packages/api`, driven by `pyproject.toml` +
  `uv.lock`); JS → **bun** (`packages/web`, driven by `package.json` + `bun.lock`).
  Run every Python tool through the project env with `uv run …`; run JS scripts with
  `bun run …` and one-off binaries with `bunx …`. **Do not** fall back to `pip`/`npm`
  for installs — it bypasses the committed lockfiles (no-silent-upgrade hard rule).
  Both lockfiles are committed.
- **Types are strict.** `uv run mypy --strict` on `packages/api`; `bunx tsc --noEmit`
  strict on `packages/web`. No `Any`/`# type: ignore` without a one-line `# reason:`.
- **Errors are typed.** Use the `ErrorRecord` discriminated stages + machine `code`
  values from SPEC §6.4. No bare `raise Exception("...")` for control flow. Define a
  **single flat** exception in `config.py`:
  `class JDParserError(Exception): def __init__(self, code: str, message: str = ""): self.code = code; self.message = message; super().__init__(f"{code}: {message}")`.
  Instantiate it directly with the `code=` kwarg at every raise site (do **not**
  create per-stage subclasses). Per-job stages catch it and convert to an
  `ErrorRecord` + a `failed` `EvaluatedJob`; they do **not** crash the run.
- **Validate every boundary object.** LLM outputs via `instructor`
  (`response_model=<Pydantic>`, validate + retry); Adzuna/extraction outputs validated
  before they enter state. `is_qualified()` (SPEC §7) is the only display gate.
- **Casing.** snake_case for all JSON (SPEC §10 item 1). FastAPI returns Pydantic
  `.model_dump()` directly; do not add camelCase aliases.
- **Serialization.** State channels carry plain dicts (`Model.model_dump()`); never
  store live Pydantic instances in LangGraph state (must be JSON-serializable for
  the checkpointer).
- **Async.** Node functions are sync (LangGraph runs them on a threadpool); the
  fan-out concurrency cap is `EVAL_FANOUT_CONCURRENCY` (set on graph compile, SPEC
  §6.1). `httpx` calls are sync within nodes. Playwright uses its **sync** API
  inside the (threadpool) node.
- **Time.** All timestamps ISO-8601 UTC via `datetime.now(timezone.utc).isoformat()`.
- **No secrets in code.** `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `OPENROUTER_API_KEY`
  from env (`.env` + `python-dotenv`). `.env` is gitignored; `.env.example` is committed.
- **Commit per phase** (BUILD.md) with a descriptive message.

## Foundations — pinned dependencies

> **No silent upgrades.** Install exactly these. If an agent thinks it needs a
> different version, it doesn't — justify in the PR or skip.

**Install (M0):**
```bash
# Python (uv) — installs runtime + dev group into packages/api/.venv from uv.lock
cd packages/api && uv sync
uv run playwright install chromium          # browser download (see brittleness)
# JS (bun)
cd ../web && bun install
```
`uv sync` is the source of truth for the Python env (creates `.venv`, respects
`.python-version` = 3.11, installs editable `jdparser` from the `pyproject.toml`
`[project]` + build backend). `bun install` writes/uses `bun.lock`. Never mix in
`pip install` / `npm install`.

### `packages/api/pyproject.toml` (probed reachable 2026-06-25 unless noted)

Versions below are the pins; they live in `[project.dependencies]` (runtime) and a
`[dependency-groups] dev` group (the `pytest`/`respx`/`mypy` rows), with a build
backend (e.g. `hatchling`) so `uv sync` installs `jdparser` editable.

| Package | Pin | Why |
|---|---|---|
| `python` | `>=3.11,<3.12` | `operator.add` reducers, `X | None` syntax |
| `langgraph` | `0.6.11` | state machine, `Send` fan-out, `MemorySaver` |
| `langchain-core` | `0.3.86` | langgraph peer dep |
| `openai` | `2.44.0` | OpenAI SDK pointed at OpenRouter (`base_url`) for all LLM calls |
| `instructor` | `1.15.3` | Pydantic-validated structured output + retry over the OpenAI client |
| `pydantic` | `2.13.4` | all schemas; structured-output validation |
| `httpx` | `0.28.1` | Adzuna + static fetch + redirect resolve |
| `playwright` | `1.60.0` | headless JS rendering fallback (needs `uv run playwright install chromium`) |
| `trafilatura` | `2.0.0` | readable main-content extraction |
| `beautifulsoup4` | `4.15.0` | JSON-LD + ATS DOM selection |
| `lxml` | `6.1.1` | bs4 parser backend |
| `pypdf` | `6.14.2` | PDF → text |
| `python-docx` | `1.2.0` | DOCX → text |
| `fastapi` | `0.128.8` | API service |
| `uvicorn[standard]` | `0.39.0` | ASGI server |
| `python-multipart` | `0.0.20` | **verify before relying** — file upload form parsing |
| `python-dotenv` | `1.1.1` | **verify before relying** — load `.env` |
| `pytest` | `8.4.2` | tests |
| `pytest-asyncio` | `1.2.0` | **verify before relying** — async API tests |
| `respx` | `0.23.1` | mock httpx (Adzuna, fetch) in tests |
| `mypy` | `1.18.2` | **verify before relying** — strict typing gate |

Transitive/runtime (not separate pip rows): `starlette` ships with `fastapi`
(`CORSMiddleware` is imported as `from fastapi.middleware.cors import CORSMiddleware`
— no extra dependency); **bun** provides the JS runtime/toolchain for Next 16 (no
separate Node install required), not a Python/JS package pin.

Brittleness notes:
- **Playwright** requires a one-time browser download: `uv run playwright install
  chromium` (and on Linux `uv run playwright install-deps`). This is **not** a uv/pip
  dep — it's a setup
  step (BUILD.md env gotchas). Failing to run it makes every Playwright fallback raise.
- **trafilatura 2.x** changed some function signatures from 1.x; use
  `trafilatura.extract(html, ...)` and pin exactly.

### `packages/web/package.json` (re-probed reachable 2026-06-26)

> These are the current **latest stable** versions on npm. There is no newer stable
> Next/React major (Next 17 / React 20 do not exist; Next 16.3 is preview-only). If
> you explicitly want the pre-release channel, `next@16.3.0-preview.5` is available —
> but do **not** pin a `-preview`/`-canary` build into this reproducible spec without
> a deliberate opt-in (it violates the no-silent-upgrade hard rule).

| Package | Pin | Why |
|---|---|---|
| `next` | `16.2.9` | App Router frontend — **latest stable** (16.3.0 is `-preview`/`-canary` only) |
| `react` | `19.2.7` | latest stable; Next 16 peer range is `^18.2.0 || ^19.0.0` (React 19 is the newest major it accepts) |
| `react-dom` | `19.2.7` | matches `react` |
| `typescript` | `6.0.3` | strict types (latest) |
| `tailwindcss` | `4.3.1` | styling — **CSS-first config** (see brittleness) |
| `@tailwindcss/postcss` | `4.3.1` | Tailwind v4 PostCSS plugin |
| `@types/react` | `19.2.17` | match `react` 19 |
| `@types/react-dom` | `19.2.3` | match `react-dom` 19 (needed for strict `tsc`) |
| `@types/node` | `26.0.1` | Node type defs (latest) |

Brittleness notes:
- **Tailwind v4 is CSS-first.** There is **no `tailwind.config.js`** by default;
  enable via `@import "tailwindcss";` in `app/globals.css` and the
  `@tailwindcss/postcss` plugin in `postcss.config.mjs`. Do **not** scaffold a v3
  `tailwind.config.js` + `content` array — that's the wrong major.
- **Next 16 + React 19** App Router: client interactivity (upload/poll) requires
  `"use client"`; gate browser-only state behind a `mounted` flag to avoid
  hydration mismatches (BUILD.md hard rule).
- `NEXT_PUBLIC_API_BASE` must be set at build/run time for the browser to reach the API.

### `.env.example` (committed)

```
OPENROUTER_API_KEY=sk-or-...
# optional OpenRouter attribution (sent as HTTP-Referer / X-Title):
OPENROUTER_APP_URL=
OPENROUTER_APP_TITLE=jdparser
ADZUNA_APP_ID=...
ADZUNA_APP_KEY=...

# --- Per-node LLM overrides (all optional; defaults shown, SPEC §6.3) ---
# <NODE> in {PROFILER, PLANNER, JD_PARSER, JUDGE}
# LLM_MODEL_PROFILER=~deepseek/deepseek-v4-flash-latest
# LLM_MODEL_PLANNER=~deepseek/deepseek-v4-flash-latest
# LLM_MODEL_JD_PARSER=~deepseek/deepseek-v4-flash-latest
# LLM_MODEL_JUDGE=~deepseek/deepseek-v4-flash-latest
# LLM_TEMP_PROFILER=0.2     LLM_TEMP_PLANNER=0.3     LLM_TEMP_JD_PARSER=0.1     LLM_TEMP_JUDGE=0.2
# LLM_MAX_TOKENS_PROFILER=8000   LLM_MAX_TOKENS_PLANNER=4000   LLM_MAX_TOKENS_JD_PARSER=6000   LLM_MAX_TOKENS_JUDGE=10000
# LLM_REASONING_PROFILER=off   LLM_REASONING_PLANNER=off   LLM_REASONING_JD_PARSER=off   LLM_REASONING_JUDGE=low

# Frontend (packages/web/.env.local):
NEXT_PUBLIC_API_BASE=http://localhost:8000
```
