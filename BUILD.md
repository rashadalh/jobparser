# BUILD — orchestrator brief

> The doc the orchestrator agent reads to drive the build. Runbook. The orchestrator
> reads this + the area doc(s) for the active phase. Source-of-truth: `SPEC.md`.

## Mission

Build the resume-driven job matcher (`PLAN.md` / `SPEC.md`): a Next.js + FastAPI +
LangGraph app that shows a user only jobs whose **full JD was extracted** and whose
requirements the resume meets with high confidence (deterministic gates, SPEC §7).
"Done" = a human uploads a resume in the browser and sees ≥1 qualified job card with
cited evidence; re-upload reports `resume_cache_hit=true` (SPEC §7.4–§7.5).

## Hard rules (override agent judgment)

1. **Doc precedence.** SPEC > IMPLEMENTATION_* > agent judgment. SPEC supersedes
   `docs/system-overview.html` (e.g. snake_case, SPEC §10 item 1).
2. **No scope creep.** Stay within SPEC §8 out-of-scope. No auth, no DB, no extra
   job sources, no SSE — until after M_final, if ever.
3. **No new dependencies / no silent upgrades.** Install exactly the pins in
   `docs/IMPLEMENTATION.md` §Foundations. Different version? It doesn't.
4. **Type strictness.** `uv run mypy --strict` (api) and `bunx tsc --noEmit` strict
   (web). No `Any`/`# type: ignore`/`any` without a one-line `# reason:` / `// reason:`.
5. **Errors are typed.** `JDParserError(code=...)` with codes from SPEC §6.4;
   per-job failures convert to `ErrorRecord` + a `failed` EvaluatedJob — they never
   crash the run. No string-keyed control flow.
6. **Contracts are spec-stable.** Field names, casing (snake_case), the four LLM
   schemas, `RunRecord` shape, and constants (SPEC §6) are not negotiable.
7. **Structured outputs via `instructor`.** All LLM calls go through OpenRouter
   (OpenAI SDK) with `instructor` `response_model=<Pydantic>` + `max_retries`
   (validate + re-ask), per SPEC §10 item 7. No provider-specific `thinking`/`effort`.
8. **Display is deterministic.** Visible jobs come from `is_qualified()` (SPEC §7),
   never from asking the LLM which to show.
9. **Tests run, not just exist.** Every `tests/` file executes green; "green" =
   green typecheck **and** green run.
10. **UI runs + hydration-safe.** Web exit-checks drive a real browser and assert
    data is present; client-only state is `mounted`-gated.
11. **Commit each phase** with a discrete, descriptive message.

## Environment gotchas (inherited verbatim by sub-agents)

- **Package managers: uv (Python), bun (JS).** Install with `uv sync` (packages/api)
  and `bun install` (packages/web); run Python tools via `uv run …`, JS via
  `bun run …` / `bunx …`. Do **not** use `pip`/`npm` — it bypasses the committed
  `uv.lock` / `bun.lock`. bun provides the JS runtime (no separate Node install).
- **Python 3.11** specifically (3.10+ syntax; LangGraph reducers). `uv` selects it
  via `packages/api/.python-version`; `uv sync` creates `.venv`.
- **Playwright browser install is a separate step:** after `uv sync`, run
  `uv run playwright install chromium` (and on Linux `uv run playwright install-deps`).
  Skipping it makes every JS-render fallback raise `FETCH_FAILED`.
- **Tailwind v4 is CSS-first** — `@import "tailwindcss";` + `@tailwindcss/postcss`;
  **no `tailwind.config.js`**. Do not scaffold v3-style config.
- **Next 16 / React 19 App Router** — interactive page needs `"use client"`;
  `mounted`-gate browser-only state.
- **Secrets via env** (`.env` for api, `.env.local` for web). `OPENROUTER_API_KEY`
  (optional `OPENROUTER_APP_URL`/`OPENROUTER_APP_TITLE`), `ADZUNA_APP_ID`,
  `ADZUNA_APP_KEY`, `NEXT_PUBLIC_API_BASE`. `.env*` gitignored.
- **PATH:** `uv run` executes tools inside the project `.venv`, so there's no manual
  `activate` and no system-Python/`uvicorn` shadowing to worry about — always invoke
  Python tooling via `uv run …` (and JS via `bun run …`/`bunx …`).

## Verification tiers (used by exit-checks)

| Tier | Proves |
|---|---|
| 1 Compile | typechecks/builds/lints clean |
| 2 Runtime data | call the thing, assert the response/data flow |
| 3 User-facing | real browser/CLI drives a flow, assert the data is actually there |

(Tier 3.5 sustained-observation is **not required** here — no time-coupled
user-visible trajectory; see SPEC §7 "Tier 3.5 applicability".)

Between phases the orchestrator does **trust-but-verify**: independently reproduce
one user-facing flow (Tier 2/3) rather than re-running the sub-agent's Tier-1 checks.

---

## Phase plan

### Phase 0 — Foundations (single)
**Inputs:** none.
**Ownership:** scaffold both packages; write `pyproject.toml` + `package.json` with
exact pins (docs/IMPLEMENTATION.md §Foundations); `config.py` with **all** SPEC §6
constants + `JDParserError`; `.env.example`; empty `data/profiles`, `data/runs`,
`data/uploads` (with `.gitkeep`); `globals.css`/`postcss.config.mjs` Tailwind v4.
**Does:** `cd packages/api && uv sync`; `uv run playwright install chromium`;
`cd packages/web && bun install`.
**Exit-check (Tier 1):** `uv run python -c "import jdparser.config"` succeeds and
prints every SPEC §6 constant (incl. `LLM_NODES`); `uv run mypy --strict
packages/api/jdparser/config.py` clean; `bun run build` in `packages/web` succeeds on
a stock page; `uv run playwright install chromium` exit 0. Confirm each pin actually
installed at the pinned version (`uv pip freeze`, `bun pm ls`).
**Commit:** "Phase 0: foundations + pinned deps".

### Phase 1 — Cache & resume text (single)
**Inputs:** Phase 0.
**Ownership:** `docs/IMPLEMENTATION_CACHE.md` files + `llm/schemas.py` stubs needed
(`Fingerprint`, `StoredResumeProfile`, `ResumeProfile`). **Locked:** snake_case,
`cache_key` formula (SPEC §3.8.4).
**Exit-check (Tier 1+2):** `uv run pytest tests/test_fingerprint.py tests/test_cache_store.py`
green; round-trip + cache-key-stability assertions pass; `extract_text` returns text
for a PDF/DOCX/TXT fixture and raises the right codes.
**Commit:** "Phase 1: resume text + profile cache".

### Phase 2 — LLM agents (single; needs OPENROUTER_API_KEY for the live smoke)
**Inputs:** Phase 1 (`schemas.py`).
**Ownership:** `docs/IMPLEMENTATION_LLM.md` files. **Locked:** provider = OpenRouter via
OpenAI SDK + `instructor` (mode JSON); per-node config from env-overridable
`config.LLM_NODES` (model/temp/`max_tokens`/reasoning) with generous defaults
(SPEC §6.3); `AdzunaQuery` `extra="forbid"`; validate-and-retry structured output;
`LLM_TRUNCATED` guard on `finish_reason=="length"`.
**Exit-check (Tier 1+2):** `uv run pytest tests/test_llm_schemas.py` green;
`uv run mypy --strict` clean; a config test confirms `LLM_MAX_TOKENS_JUDGE=123` env override flows into
`LLM_NODES["judge"]["max_tokens"]`; **live smoke** (orchestrator, gated on key):
`profile_resume(sample)` (Gemini Flash Lite) → valid `ResumeProfile` with ≥1 evidence;
`parse_jd_requirements(sample)` (Gemini Flash Lite) → valid `JobRequirements`;
`judge_fit(...)` (Gemini Flash Lite) → valid `FitJudgment`.
**Commit:** "Phase 2: OpenRouter LLM agents + schemas".

### Phase 3 — Adzuna + Phase 4 — Extraction (parallel-2 then integration)
**Inputs:** Phase 1 (`schemas.py`). Independent file sets → run in parallel.
- **Agent A (Adzuna):** `docs/IMPLEMENTATION_ADZUNA.md` files. Owns `adzuna/*`,
  `tests/test_dedupe.py`. Forbid edits to `extract/`.
- **Agent B (Extraction):** `docs/IMPLEMENTATION_EXTRACT.md` files. Owns `extract/*`,
  `tests/test_extract.py`, `tests/test_quality.py`. Forbid edits to `adzuna/`.
**Exit-check (Tier 1+2):** both suites green; `dedupe` collapses dupes; `extract_jd_text`
picks JSON-LD→ATS→readable in order on fixtures; `check_quality` enforces
`MIN_JD_CHARS`/noise. Orchestrator integration: a recorded Greenhouse page →
`extract_jd_text` ≥ `MIN_JD_CHARS`; Playwright fallback verified once against a known
JS listing (Tier-2 manual, documented).
**Commit:** "Phase 3+4: Adzuna discovery + JD extraction".

### Phase 5 — Graph (single)
**Inputs:** Phases 2,3,4.
**Ownership:** `docs/IMPLEMENTATION_GRAPH.md` files. **Locked:** `Send` fan-out, reducer
lifecycle (SPEC §3.10), `profile` injection into each `Send` payload (SPEC §3.2),
`is_qualified`
verbatim (SPEC §7), `max_concurrency=EVAL_FANOUT_CONCURRENCY`.
**Exit-check (Tier 2):** `uv run pytest tests/test_graph_smoke.py` green (faked adzuna/
extract/llm). Each acceptance scenario gets a dedicated assertion:
- `len(evaluated_jobs)==len(deduped_jobs)` and qualified subset == `is_qualified` (§7.6).
- **§7.1:** a job with valid judgment but `requirements=None` (forced parse failure)
  is **excluded** from `qualified_jobs`; a job with `final_url=None` is excluded.
- **§7.2:** a job whose judge returns a non-empty `failed_dealbreakers` (or
  `missing_hard_requirements`) is **excluded** even with `decision="qualified"`.
- **§7.3:** a job with `decision="qualified"` but `confidence=0.6` is excluded
  (lands in `rejected` with `status="uncertain"`); `confidence=0.8` is included.
- **§7.4:** with a pre-seeded cache file, a run has `resume_cache_hit True` **and** a
  spy confirms `profile_resume` was **not called** (monkeypatch a counter); on a cold
  cache it **is** called exactly once.
- forced extract failure → `failed` EvaluatedJob with correct `failure_stage`.
- forced reducer-count mismatch → run raises `EVAL_COUNT_MISMATCH` (does not complete).

Orchestrator runs `uv run python -m jdparser tests/fixtures/sample_resume.pdf` against
**live** Adzuna+OpenRouter: completes without unhandled exception; second run prints
`cache_hit=true`.
**Commit:** "Phase 5: LangGraph state machine + subgraph".

### Phase 6 — API (single)
**Inputs:** Phase 5.
**Ownership:** `docs/IMPLEMENTATION_API.md` files. **Locked:** pull-only polling, run-record
lifecycle (SPEC §3.10), partitioning of evaluated jobs into
qualified/failures/rejected, never-stuck-running guard.
**Exit-check (Tier 2):** `uv run pytest tests/test_api.py` green (graph faked): POST→202,
background drives to `completed`, GET returns partitioned `RunRecord`; unknown id →
404; fatal error → `failed` with `error`; `/api/health` ok. Orchestrator: start
`uv run uvicorn jdparser.server:app --port 8000`, `curl -F file=@sample.pdf POST
/api/runs`, poll `GET` to terminal, assert `qualified_jobs` present (Tier-2 against live pipeline).
**Commit:** "Phase 6: FastAPI run service".

### Phase 7 — Web (single)
**Inputs:** Phase 6.
**Ownership:** `docs/IMPLEMENTATION_WEB.md` files. **Locked:** snake_case types, Tailwind
v4, `mounted`-gating, cited-evidence JobCard, FailuresPanel discloses MVP stubs.
**Exit-check (Tier 1+3):** `bun run build` + `bunx tsc --noEmit` clean. Orchestrator Tier-3
(real browser via the available browser tooling): with api+web running, upload
`sample_resume.pdf`, wait for completion, assert ≥1 JobCard with a clickable
`final_url` and ≥1 cited evidence line is present in the DOM; failures appear only in
FailuresPanel; no hydration warning.
**Commit:** "Phase 7: Next.js frontend".

### Phase M_final — Polish (orchestrator-driven)
**Inputs:** Phases 0–7.
**Does:** fill `README.md` (run instructions, env, the Playwright + Tailwind v4
gotchas, definition-of-done steps); confirm audit-view copy discloses the SPEC §9
stubs; final clean typecheck/build across both packages.
**Exit-check (Tier 3, definition of done):** the full SPEC §7.5 browser flow + the
SPEC §7.4 cache-hit re-run, reproduced by the orchestrator end-to-end.
**Commit:** "M_final: README + end-to-end verification".

## Sub-agent dispatch rules

Each sub-agent prompt MUST include: (1) the exact section refs (e.g. "implement per
docs/IMPLEMENTATION_EXTRACT.md §fetch.py + SPEC §3.8.2"); (2) **files owned** + files it
must NOT touch; (3) pre-existing on-disk surface to import (schemas, config
constants) rather than redefine; (4) the relevant Hard Rules subset; (5) locked
decisions for the phase; (6) env gotchas (Playwright/Tailwind/Python 3.11);
(7) reporting format (below). Parallel agents (Phase 3+4) have **non-overlapping**
file ownership; `schemas.py` already exists on disk before they spawn.

Review/audit agents are read-only (the Step-7 doc review and any code review):
findings only; a separate fix agent applies them.

## Failure handling

A phase exit-check fails → the orchestrator (a) reproduces the failure itself,
(b) reads the sub-agent's report + diff, (c) dispatches a targeted fix with the
exact failing assertion. Iteration budget: **3 fix attempts** per phase; after that,
stop and report the blocker with the reproduction. Never mark a phase done on a
sub-agent's self-report alone — independently reproduce one flow (trust-but-verify).

## What "done" means (final checklist — all true)

- [ ] All phase exit-checks pass, reproduced by the orchestrator.
- [ ] `uv run pytest` green across `packages/api/tests`; `uv run mypy --strict` clean.
- [ ] `bun run build` + `bunx tsc --noEmit` clean in `packages/web`.
- [ ] Browser flow: upload → ≥1 qualified JobCard with `final_url` + cited evidence.
- [ ] Re-upload same resume → run reports `resume_cache_hit=true`, no profiler call.
- [ ] A thin/blocked JD → job absent from feed, present in FailuresPanel with stage.
- [ ] `uncertain`/sub-0.75 jobs absent from feed, present in rejected/audit.
- [ ] Pins match `docs/IMPLEMENTATION.md` (`uv pip freeze`/`bun pm ls`); Playwright chromium installed.
- [ ] README documents env + the two gotchas + definition-of-done steps.

## Final report format

```
BUILD COMPLETE — Resume-Driven Job Matcher
Phases: 0–7 + M_final  [✅/❌ each]
Exit-checks reproduced by orchestrator: [list, tier]
Definition of done (SPEC §7.5/§7.4): [PASS/FAIL + how verified]
Tier-3 browser check: [PASS/FAIL] — [what was asserted in the DOM]
Known limitations / MVP stubs in effect: [SPEC §9 list]
If any Tier-3 check could not be run by the orchestrator: [exact manual steps for the user] — and DONE is NOT claimed.
Commits: [one per phase]
```
