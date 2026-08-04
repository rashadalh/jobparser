# PLAN — Resume-Driven Job Matching

> Audience: humans orienting on the project. One page. Stable across phases.
> Source overview: [`docs/system-overview.html`](docs/system-overview.html). This doc set is
> derived from that overview via the `derive-build-plan` method.

## Mission

A web app that takes a candidate's resume, discovers jobs through the Adzuna API,
extracts each job's **full** description as evidence, and uses specialized
open-weight LLM nodes via OpenRouter (orchestrated by LangGraph) to parse
requirements and judge fit — showing the user **only** jobs where the JD was
actually extracted and the resume appears to meet the stated requirements with
high confidence.

The product promise is narrow on purpose: no speculative matches. A job is shown
only if (1) its full JD was extracted and validated, and (2) a strict fit judge
returns `qualified` with confidence ≥ 0.75, backed by cited resume evidence.

## In scope

- Resume upload (PDF / DOCX / plain text) → normalized text.
- A durable **resume-profile cache** (JSON flat-files) keyed by content + parser/schema version,
  so a known resume skips the expensive profiler LLM call.
- LangGraph state machine: extract → fingerprint → load-or-parse profile → plan
  searches → Adzuna search → dedupe → **fan-out job evaluation** → aggregate.
- Job-evaluation subgraph per job: resolve final URL → fetch (static HTTP, with
  Playwright headless fallback) → extract JD (JSON-LD → ATS parsers → readable
  text) → parse requirements → judge fit (all Gemini 3.1 Flash Lite).
- FastAPI service bridging the browser to the graph (start run, poll status/results).
- Next.js frontend: upload a resume, watch run progress, see qualified job cards
  with cited evidence, plus an audit view of failures.

## Out of scope (MVP)

- Authentication / multi-tenant accounts (a single fixed `local` user).
- Applying to jobs, saving searches, or any write-back to job boards.
- A SECOND job source. The `JobSource` seam exists (`jobsource/base.py`) and Adzuna is
  the only implementation — the abstraction is in place, multi-source is not.
- A real database (resume cache and run records are JSON flat-files).
- Push/streaming progress (WebSocket/SSE); the frontend **polls**.
- Letting the LLM decide which jobs to display — the visible set is produced by
  deterministic code applying the qualification gates.

## Major work surfaces (one IMPLEMENTATION doc each)

| Surface | Doc | Owns |
|---|---|---|
| Orchestration | `docs/IMPLEMENTATION_GRAPH.md` | LangGraph state, top-level nodes, fan-out, evaluation subgraph |
| LLM agents | `docs/IMPLEMENTATION_LLM.md` | The 6 OpenRouter nodes (all Gemini 3.1 Flash Lite): schemas, prompts, routing, structured output |
| Job discovery | `docs/IMPLEMENTATION_ADZUNA.md` | Adzuna client, search execution, dedupe |
| Evidence extraction | `docs/IMPLEMENTATION_EXTRACT.md` | URL resolve, fetch (httpx+Playwright), JSON-LD/ATS/readable, quality checks |
| Profile cache | `docs/IMPLEMENTATION_CACHE.md` | Fingerprinting, JSON flat-file store, resume text extraction |
| API service | `docs/IMPLEMENTATION_API.md` | FastAPI run lifecycle, background execution, status/results |
| Frontend | `docs/IMPLEMENTATION_WEB.md` | Next.js upload, polling, results + audit UI |

## Definition of done (what a human does to confirm it works)

In a browser at the running web app: upload a known resume PDF, wait for the run
to complete, and see **≥1 qualified job card** showing company, title, the
resolved job URL, and resume evidence cited for each met requirement. Re-uploading
the same resume completes a run that reports `resume_cache_hit = true`. Jobs whose
JD could not be extracted, or that the judge marked `uncertain` / below
confidence, do **not** appear in the main feed (they appear in the audit view).

## Source-of-truth ordering

`SPEC.md` > `IMPLEMENTATION_<AREA>.md` > agent judgment. `SPEC.md` supersedes any
illustrative snippet in `docs/system-overview.html` (e.g. the TypeScript
`StoredResumeProfile` there is illustrative; SPEC §3 defines the canonical
snake_case record).
