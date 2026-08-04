# REFACTOR AUDIT — jdparser

> ## STATUS: EXECUTED — all 8 phases landed
>
> Branch `refactor/audit-938203f`, PR
> [#1](https://github.com/rashadalh/jdparser/pull/1). Everything below is the plan as
> approved — a record of what was decided and why, not work outstanding. Read it for the
> **reasoning** behind a design; read `git log` for what shipped.
>
> | Phase | Commit | Findings |
> |---|---|---|
> | 0 — Land the audit | `1320b4c` | F6 (partial) |
> | 1 — Dedupe null crash | `de67d7c` | F1 |
> | 2 — `Job` domain model | `6033bdb` | F2 (shape leak) |
> | 3 — Job-source seam | `1bbcb64` | F2 (naming, prompt) |
> | 4 — Test structure | `424b13c` | F3, F13 |
> | 5 — Prompts to files | `222054d` | F8, F14 |
> | 6 — Web DRY + type drift | `0ef9bc3` | F9, F10, F11, F12 |
> | 7 — Docs re-sync | `7b1ee50` | F4, F5, F6, F7, F15, F16 |
>
> Not fixed, by decision: **F16** (no split of `server.py`/`nodes.py` — revisit past ~500
> lines) and **F17** (duplicate one-line `_WS` regex). Both have their reasoning in §2.
>
> Final state: `pytest` 149 passed (from 137), `mypy --strict` clean, `bun run typecheck`
> and `bun run build` clean.
>
> **One deviation from the plan:** §5.4 Phase 4 specifies `tests/conftest.py`; the shared
> builders shipped as `tests/builders.py` instead. They are plain functions, not pytest
> fixtures or hooks, and importing names out of `conftest` works by sys.path accident
> rather than by design.

> **Pinned to commit `938203fd2f19f6e3e0f631f2963713b87229a42b`**
> ("Fix match-quality bugs found via live testing; add candidate feedback loop", branch `main`).
> Every `file:line` citation below refers to that tree — **not** to HEAD. Phases 2–4 split,
> renamed, and moved most of the files cited here (`adzuna/` → `jobsource/adzuna/`, the
> prompts out of `llm/*.py`, `test_graph_smoke.py` four ways), so resolve any citation with
> `git show 938203f:<path>` rather than opening the working tree and finding it changed.

This document was written to be read **cold**, by an agent with no memory of the audit
conversation: findings, designs, phase order, branch/commit rules, and the exact
verification commands per component.

---

## 1. Lay of the land

**What it is.** A resume-driven job matcher. Upload a resume → extract text → cache/parse a
structured `ResumeProfile` → LLM plans job-board queries → search Adzuna → dedupe →
cheap relevance pre-screen → fan out per job (resolve URL → fetch → extract JD → quality
gate → parse requirements → judge fit) → show only jobs that pass a deterministic display
gate, with cited resume evidence.

**Components.**

| Component | Path | Stack |
|---|---|---|
| API | `packages/api` | Python 3.11, FastAPI, LangGraph 0.6.11, Pydantic 2, instructor + OpenRouter |
| Web | `packages/web` | Next.js 16, React 19, TypeScript 6, Tailwind 4, bun |
| Docs | repo root + `docs/` | 12 markdown files at root, 1 HTML overview in `docs/` |

**Python package layout** (`packages/api/jdparser/`): `adzuna/` (client, dedupe),
`cache/` (fingerprint, store), `extract/` (fetch, jsonld, ats, readable, quality, resolve),
`graph/` (state, nodes, subgraph, build), `llm/` (client, schemas, 6 agent modules),
`resume/`, `runs/`, plus top-level `config.py`, `server.py`, `experience.py`, `__main__.py`.

**Excluded from this audit** (generated / vendored / runtime artifacts, per `.gitignore`):
`packages/api/.venv/`, `packages/web/node_modules/`, `.next/`, `__pycache__/`,
`.pytest_cache/`, `.mypy_cache/`, `packages/api/data/` (runtime state), `uv.lock`,
`bun.lock`, `packages/api/tests/fixtures/`.

**Working-tree state at audit time.** `git status` shows exactly one entry: `?? CLEANUP.md`
(untracked). It is **not** in-flight feature work — it is a stale plan document whose six
items all landed in commit `b5a7d6d` ("Remove over-engineering: 6 complexity-only
refactors"). Verified: `get_client`, `resume_profile_id`, `file_hash`, and `text_hash` no
longer appear anywhere in `packages/api/jdparser/`; `graph/subgraph.py:217` has the
`make_route` factory; `components/Pills.tsx` exists; `graph/state.py:74` has
`initial_state`. **No refactor target below has uncommitted changes**, so there is no
collision to resolve. CLEANUP.md is deleted in Phase 0; its content is preserved verbatim in
`b5a7d6d`'s commit message.

**Baseline (measured at the pinned SHA, before any change):**

```
cd packages/api && uv run pytest            → 137 passed, 1 warning
cd packages/api && uv run mypy --strict jdparser → Success: no issues found in 36 source files
```

### 1.1 Scope decisions

- **Test files:** in scope under a **laxer threshold** — flagged only when they mix unrelated
  concerns or duplicate fixtures, not on line count alone. (Owner decision. This is why
  `tests/test_api.py` at 329 lines and `tests/test_llm_schemas.py` at 292 lines are
  explicitly *not* flagged, while `tests/test_graph_smoke.py` at 631 lines is — see F13.)
- **Adzuna:** `PLAN.md:47` lists "Job sources other than Adzuna" as out of scope for MVP.
  The owner's decision is nevertheless to **flag the hardcoding, design a provider seam, and
  build it** (F2/§4), because the cost of a second source is not the client module — it is
  the raw-dict shape that has leaked into five unrelated modules. Both halves are approved:
  the leak fix (Phase 2) and the seam (Phase 3). They stay separate phases because the leak
  fix is what resolves F1's root cause and stands on its own merits — folding it into a large
  rename would bury a live bug fix inside structural churn.

  **Rationale for building the seam now (owner decision — do not re-litigate mid-execution).**
  The code evidence on its own argues for deferring Phase 3: there is exactly one source, no
  second one is planned, and the seam churns node names, state channels, and every doc that
  cites them. The audit's own recommendation was to hold it. The owner reviewed that
  recommendation and chose to execute now. An agent reading only the code will see the
  deferral case and be tempted to unwind the abstraction as speculative — it is not
  speculative, it is a deliberate call made with the deferral case in full view. `PLAN.md:47`
  is updated in Phase 7 so the plan and the code stop disagreeing.
- **Naming:** no owner-supplied terminology hints; drift was found by cross-checking code
  against `SPEC.md` and the `IMPLEMENTATION_*.md` set (F4, F5).

### 1.2 Mechanical inventory — every file over 200 lines

Every file below is evaluated. Nothing on this list is sampled or skipped.

| Lines | File | Verdict |
|---:|---|---|
| 972 | `SPEC.md` | Doc — covered by F5, F6, F15 |
| 876 | `docs/system-overview.html` | Doc — covered by F6 |
| 631 | `packages/api/tests/test_graph_smoke.py` | **Flagged — F13** |
| 371 | `packages/api/jdparser/server.py` | **Flagged (low) — F16** |
| 349 | `packages/web/components/FailuresPanel.tsx` | **Flagged — F9** |
| 329 | `packages/api/tests/test_api.py` | Reviewed, not flagged — one concern (the HTTP surface), mirrors `server.py`'s endpoints one-for-one |
| 325 | `packages/api/jdparser/graph/nodes.py` | **Flagged (low) — F16**; also the site of F1, F2 |
| 292 | `packages/api/tests/test_llm_schemas.py` | Reviewed, not flagged — one concern (schema validation); contributes to F3 |
| 272 | `packages/web/app/page.tsx` | **Flagged — F10** |
| 272 | `packages/api/jdparser/llm/schemas.py` | Reviewed, not flagged — deliberately the single owner of every model (`SPEC §3`); splitting it would create the import cycle it exists to prevent |
| 271 | `packages/web/components/SavedPanel.tsx` | Reviewed, not flagged — one cohesive concern (the saved-resume/run-history panel); length is JSX, not logic |
| 252 | `IMPLEMENTATION_GRAPH.md` | Doc — covered by F4, F6 |
| 235 | `BUILD.md` | Doc — covered by F4, F6 |
| 233 | `IMPLEMENTATION_LLM.md` | Doc — covered by F4, F5, F6 |
| 221 | `packages/api/jdparser/graph/subgraph.py` | Reviewed, not flagged — one concern (the per-job stage machine); contains F2 citation sites but is not itself the problem |

No folder was skipped on size. `packages/web/` (13 authored files) and
`packages/api/jdparser/extract/` (7 files) were cross-compared file-against-file, not just
read individually — that comparison is what produced F9 and F17.

---

## 2. Findings

Each finding carries a concrete citation and a phase. Nothing here is a suggestion without
a home; the traceability table in §6 checks both directions.

### F1 — `dedupe.py` and its two copies have already diverged, and the divergent one crashes

**Category:** DRY violation, already-diverged subclass → correctness bug. **Severity: high.**
**Phase 1.**

The raw job dict is destructured in three places. Two are null-safe, one is not:

- `packages/api/jdparser/graph/subgraph.py:55` — `(job.get("company") or {}).get("display_name")` ✅
- `packages/api/jdparser/graph/nodes.py:204` — `(j.get("company") or {}).get("display_name", "")` ✅
- `packages/api/jdparser/adzuna/dedupe.py:18` — `job.get("company", {}).get("display_name")` ❌
- `packages/api/jdparser/adzuna/dedupe.py:20` — `job.get("location", {}).get("display_name")` ❌

`.get(k, {})` returns the default only when the key is **absent**. When Adzuna returns the
key with an explicit `null` — which its API does — it returns `None`, and the chained
`.get` raises. Verified at the pinned SHA:

```
>>> dedupe([{"id":"1","title":"X","company":None,"location":None,"redirect_url":"u"}])
AttributeError: 'NoneType' object has no attribute 'get'
```

**Failure scenario.** A single search result with `"company": null` crashes `dedupe_jobs`
(`graph/nodes.py:196`). That is a top-level node, not inside the fault-isolated fan-out, and
it has no `try/except` — the exception propagates out of `graph.stream`, is caught by
`server._execute`'s broad handler (`server.py:187`), and the **entire run** ends `failed`
with a bare `AttributeError` string. Every other job in the pull is lost. The user sees no
matches and no actionable error.

The two null-safe copies prove the correct form was known; `dedupe.py` simply never got the
fix. This is why F2 (one shared accessor) is the root-cause fix rather than patching this
one line — but the one line gets patched in Phase 1 anyway, because Phase 2 is a larger
change and this is a live crash.

### F2 — Adzuna is a branchless hardcoded assumption; its dict shape has leaked into five modules

**Category:** hardcoded assumption that should be an abstraction (branchless form).
**Phases 2 and 3.**

There is no flag, no branch, and no second implementation anywhere — which is exactly why
this is invisible on a cold read of any single file. It only appears when you ask what a
second job source would cost.

**Pattern amplification (Step 4 — the whole codebase was searched, not just the first hit).**
`adzuna` appears in **37 files**. Split by layer:

*Legitimate — a provider client should be provider-shaped:*
`adzuna/client.py`, `adzuna/dedupe.py`, `IMPLEMENTATION_ADZUNA.md`, `tests/test_adzuna_client.py`.

*Leaked — a provider name or shape inside a provider-agnostic layer:*

| Site | Leak |
|---|---|
| `llm/schemas.py:91` | `AdzunaQuery` — the provider's query language is a first-class model in the shared schema module |
| `graph/state.py:41-42` | `search_plan: list[AdzunaQuery.model_dump()]`, `adzuna_results` — provider name in generic state channels |
| `graph/nodes.py:178` / `graph/build.py:80,91` | node named `run_adzuna_search` |
| `llm/search_planner.py:105` | `plan_adzuna_queries` |
| `llm/search_planner.py:12-102` | the planner **prompt** encodes Adzuna's query semantics: `what` ANDs terms, `what_or` ORs and is ANDed against `what`, `where` must be geographic or returns zero, `results_per_page × pages` is a fixed budget. This is the deepest coupling in the system — a second provider needs a different prompt, not a different client. |
| `graph/nodes.py:101-142` | `_NON_GEO_LOCATIONS` and `_apply_location_override` exist solely to work around Adzuna's `where` geocoding |
| `config.py:71` | `JD_FETCH_REFERER = "https://www.adzuna.com/"` — a provider URL inside the generic fetch layer, consumed by `extract/fetch.py:92` and `extract/resolve.py:26` for **every** page fetched, including employer sites that have nothing to do with Adzuna |
| `llm/schemas.py:230` | `ErrorStage` includes the literal `"adzuna_search"` — **persisted** in `data/runs/*.json` |
| `extract/resolve.py:3-4` | `resolve_final_url` is documented as following an Adzuna redirect chain |

**The shape leak (the expensive part).** The raw provider dict is destructured, with
per-site null-handling, in five modules — there is no `Job` domain model:

- `adzuna/dedupe.py:17-20` — `final_url`/`redirect_url`, `company.display_name`, `title`, `location.display_name`
- `graph/subgraph.py:53-56` — `id`, `title`, `company.display_name`, `location.display_name`
- `graph/subgraph.py:141` — `redirect_url`
- `graph/subgraph.py:190` — `title`
- `graph/nodes.py:202-207` — `id`, `title`, `company.display_name`, `location.display_name`
- `graph/nodes.py:242-267` — `id` (six separate `str(j.get("id"))` calls)
- `llm/screener.py:69-72` — `id`, `title`, `company.display_name`, `description`
- `server.py:361` — `title`

F1 is the first consequence of this leak. It will not be the last: every one of these sites
is an independent opportunity for the null-handling to diverge again.

**Second branchless hardcode in the same area.** `config.py:72` — `ADZUNA_COUNTRY = "us"`.
The system is US-only, and this is stated as MVP-fixed (`SPEC §8/§9`), so it is **not**
proposed for change. It is recorded here so a future reader does not mistake its absence
from the design for an oversight. See §4.3.

### F3 — Six copies of the `ResumeProfile` test fixture; no `conftest.py`

**Category:** DRY violation. **Phase 4.**

`packages/api/tests/` has no `conftest.py` (verified with `ls -a`). `ResumeProfile(...)` is
hand-built in six places and `StoredResumeProfile(...)` in five:

- `tests/test_llm_schemas.py:41` and `tests/test_screener.py:15` — **byte-identical** bodies
- `tests/test_graph_smoke.py:51`, `tests/test_cache_store.py:18`, `tests/test_api.py:229`,
  `tests/test_api.py:261` — near-identical, differing only in `total_years_experience`,
  `education`, and evidence strings
- `StoredResumeProfile`: `tests/test_graph_smoke.py:68`, `tests/test_cache_store.py:32`,
  `tests/test_api.py:267`, `tests/test_llm_schemas.py:128`, `tests/test_llm_schemas.py:223`

`ResumeProfile` has 12 required fields. Adding a thirteenth means editing six fixtures in
five files — and the schema has already been extended twice (`SCHEMA_VERSION = "1.2.0"`,
`config.py:89`: education at 1.1.0, work_periods at 1.2.0), so this cost has been paid twice
already.

### F4 — The GLM → Gemini rename was applied to code and abandoned in docs

**Category:** terminology drift. **Phase 7.**

The logic model was switched from `z-ai/glm-5.2` to `google/gemini-3.1-flash-lite` and the
constant renamed `MODEL_GLM` → `MODEL_LOGIC` (`config.py:93`). The docs were not updated. A
partial rename is worse than none — code and docs now contradict each other, and one doc
line contradicts *itself*:

- `SPEC.md:940` — "**gemini-3.1-flash-lite** (`z-ai/glm-5.2`) for logic nodes". The display
  name was replaced; the slug in parentheses was not. Self-contradicting on one line.
- `SPEC.md:790` — example reads `LLM_MODEL_JUDGE=z-ai/glm-5.1`. A **third** version string,
  matching neither the old code nor the new.
- `SPEC.md:969` — pin-validation section still certifies `z-ai/glm-5.2` as confirmed present and priced.
- `IMPLEMENTATION_GRAPH.md:53` — references `MODEL_GLM`, **a symbol that no longer exists**
  (`grep -rn MODEL_GLM packages/api/jdparser` → 0 hits). Any agent following this doc writes
  a `NameError`.
- Still say "GLM 5.2": `IMPLEMENTATION_LLM.md:6,14,146,159,194,228`,
  `IMPLEMENTATION_GRAPH.md:46,60,145`, `IMPLEMENTATION.md:163,164,166`, `BUILD.md:115,117`,
  `README.md:10`, `PLAN.md:29,49`.
- Test fixtures carry the stale slug as a literal value: `tests/test_llm_schemas.py:135,225`,
  `tests/test_cache_store.py:39`, `tests/test_graph_smoke.py:75`. Harmless (it is opaque
  string data) but it keeps the dead name searchable and alive.

**Consequence in code, not just docs.** `MODEL_LOGIC` (`config.py:93`) and
`MODEL_GEMINI_FLASH_LITE` (`config.py:94`) are now **the same literal string**. The two-tier
routing that `config.py:91` ("LLM constants & model routing") and `SPEC §6.3` describe is
currently cosmetic: all six nodes resolve to one model. This is a legitimate state to be in
— but it is undocumented, and a reader of either constant name will believe otherwise.

### F5 — `SPEC.md` no longer is the single source of truth it declares itself to be

**Category:** missing/stale documentation. **Phase 7.**

`config.py:1` states "SPEC §6 single source of truth". Five constants live only in code:

| Constant | Defined | In `SPEC.md`? |
|---|---|---|
| `SCREEN_EVAL_CAP` | `config.py:79` | no |
| `SCREEN_BATCH_SIZE` | `config.py:84` | no |
| `SEARCH_MAX_DAYS_OLD_DEFAULT` | `config.py:52` | no |
| `BOILERPLATE_LINE_MAX_WORDS` | `config.py:48` | no |
| `JD_FETCH_REFERER` | `config.py:71` | no |

Three shipped, user-visible features have **zero** SPEC coverage: the relevance pre-screen's
`screened_out` audit bucket (`graph/state.py:44`, surfaced in `FailuresPanel.tsx:244-321`),
the candidate feedback loop (`candidate_notes`, `graph/state.py:34`; `POST /api/feedback`,
`server.py:336`), and the recruitment-agency filter (`include_agencies`,
`graph/state.py:40`). All three post-date the SPEC.

Related count drift: `llm/client.py:3` and `IMPLEMENTATION_LLM.md:1` both say "the four LLM
agents". There are **six** (`config.py:132-142`: profiler, planner, jd_parser, judge,
screener, feedback).

### F6 — Doc sprawl: 12 markdown files at the repo root, one HTML in `docs/`

**Category:** spec/plan/doc sprawl. **Phase 7.**

Root: `SPEC.md`, `PLAN.md`, `BUILD.md`, `README.md`, `IMPLEMENTATION.md`, and seven
`IMPLEMENTATION_<AREA>.md` files. `docs/` holds a single 876-line `system-overview.html`
that `PLAN.md:5` names as the source everything else derives from. There is no canonical
home; a newcomer's first `ls` shows twelve docs and cannot tell which is authoritative.

Plus one stale doc: **`CLEANUP.md` (untracked)** describes six refactors that all landed in
`b5a7d6d`. Its "Verify" section (lines 225-226) instructs a reader **not** to touch two
items it calls out of scope — one of which is the duplicate `MODEL_*` constant that F4 shows
is now a live documentation problem. Leaving it in the tree means the next agent reads an
instruction not to look at it.

### F7 — No orientation README in any of the seven `jdparser/` subpackages

**Category:** missing subfolder orientation docs. **Phase 7.**

`find packages -name "README*"` returns only vendored hits under `.venv/`. None of
`adzuna/`, `cache/`, `extract/`, `graph/`, `llm/`, `resume/`, `runs/` carries one. The
module docstrings are genuinely good — but they are per-file, so orienting in `graph/`
(four files, ~750 lines, the heart of the system) means reading all four. A new contributor
or agent has no one-screen entry point below the repo root.

### F8 — ~250 lines of prompt prose embedded in six Python modules

**Category:** large text blobs in source. **Phase 5.**

| Prompt | Lines | Note |
|---|---:|---|
| `llm/fit_judge.py:16-107` | 92 | The single densest piece of business logic in the system — thematic fit, seniority asymmetry, education, evidence rules |
| `llm/search_planner.py:12-102` | 91 | **Complicated move:** ends with runtime `% {"max_pages": ADZUNA_MAX_PAGES, "max_queries": SEARCH_PLAN_MAX_QUERIES}` (`search_planner.py:102`). A plain file move loses the interpolation; the extraction must preserve it. |
| `llm/screener.py:23-53` | 31 | |
| `llm/resume_profiler.py:12-44` | 33 | |
| `llm/feedback.py:17-42` | 26 | |
| `llm/jd_parser.py:11-33` | 23 | |

These are the highest-churn, most-reviewed lines in the repo (the last three commits are all
prompt-tuning), and they are the hardest to diff inside backslash-continued Python string
literals.

### F9 — `FailuresPanel.tsx` renders the same screened-job list three times

**Category:** DRY violation. **Phase 6.**

`components/FailuresPanel.tsx:255-267`, `:281-293`, and `:307-319` are three copies of the
identical `<li>` body (title, ` · company`, ` · location` in a gray span), differing only in
the array mapped. The enclosing `<section>` + `<h4>` + explainer `<p>` scaffolding is a
fourth near-copy. The three buckets are derived one line apart at `:189-193`.

Adding a fourth screen-out reason means a fourth copy. There have already been three
(`off_field`, `over_cap`, `agency` — `graph/nodes.py:206`).

### F10 — Four copies of the same reset preamble in `page.tsx`

**Category:** DRY violation. **Phase 6.**

`app/page.tsx:96-101`, `:115-121`, `:143-148`, `:167-171` each open with the same
`stopPolling()` / `setError(null)` / `setTimedOut(false)` / `setRun(null)` /
`setParsedProfile(null)` sequence. They are **already inconsistent**:
`handleSubmit` (`:99`) and `handleRunFromProfile` (`:146`) call `setRun(null)`;
`handleOpenRun` (`:167-171`) does not — it deliberately keeps the old run visible until the
new one loads. `handleParse` (`:115-121`) additionally calls `setPhase("idle")` where the
others set `"starting"`. Some of that divergence is intentional and some is likely not, and
the copy-paste shape makes it impossible to tell which from reading.

### F11 — `lib/types.ts` is a hand-maintained mirror of `schemas.py` and has already drifted

**Category:** DRY violation across the language boundary. **Phase 6.**

`packages/web/lib/types.ts` restates every Pydantic model by hand, with no codegen and no
check. `StoredResumeProfile` (`lib/types.ts:46-49`) declares 6 fields; the Python model
(`llm/schemas.py:77-87`) has 10 — `user_id`, `parser_version`, `schema_version`, and `notes`
are missing. `parseResume` (`lib/api.ts:41`) returns this type, so the frontend is typed
against a lie: those fields arrive on the wire and TypeScript denies they exist.

This is currently harmless (nothing reads them) and a full codegen pipeline is not warranted
for a 78-line file. The fix is a check, not a generator — see Phase 6.

### F12 — The agency-ordering rule is implemented on both sides of the wire

**Category:** DRY violation. **Phase 6.**

`graph/nodes.py:254` orders `directs + agencies` server-side so the evaluation budget fills
with direct employers first. `components/QualifiedJobs.tsx:9` re-derives the same ordering
client-side over the already-ordered list. Not yet diverged — the client re-sort is a no-op
on server-ordered data — but two implementations of one product rule, and
`QualifiedJobs.tsx:4-6` claims "the agency relevance decision lives server-side", which the
line below it contradicts.

### F13 — `test_graph_smoke.py` holds five unrelated test concerns; one has its own file too

**Category:** god file (test, judged on responsibility count per §1.1). **Phase 4.**

631 lines, the largest authored file in the repo, covering: graph smoke/count invariants
(`:308-359`), `is_qualified` units (`:329`), agency screening (`:360-388`), location
override (`:448-563`), and screener/cap behavior (`:565-631`), on top of ~200 lines of
private builders (`:42-305`).

The location-override block (`:448-563`) tests the same function —
`nodes._apply_location_override` — that `tests/test_location_override.py` (65 lines) exists
for. Two files, one concern, no stated boundary between them.

### F14 — Mutable default argument in `judge_fit`

**Category:** latent correctness. **Phase 5** (same file as F8's largest prompt).

`llm/fit_judge.py:114` — `notes: list[CandidateNote] = []`. The list is never mutated
(`:121` only reads it), so there is no live bug. It is one line to fix and it is the kind of
default that becomes a bug the moment someone appends to it.

### F15 — `SPEC.md` duplicates `is_qualified`'s source verbatim, and the copy has drifted

**Category:** DRY violation (docs). **Phase 7.**

`SPEC.md:876-891` contains the full body of `is_qualified`, and `graph/nodes.py:310-311`
states it is "copied verbatim from SPEC §7". The copies have already diverged: the SPEC
version carries `# §7.2: redundant thematic-fit guard` on the `thematic_fit` line; the code
does not. Cosmetic today, but this is a **normative** gate — the product's central promise
("no speculative matches") is enforced by exactly these ten conditions, and there are two
places to change them.

### F16 — Two files mix responsibilities without crossing the line into a split (low priority)

**Category:** god file. **Phase 7** (documentation only — no split proposed).

- `jdparser/server.py` (371) — HTTP routing *and* graph execution (`_execute`, `:113-188`),
  progress streaming (`_stream_progress`, `:86-111`), and result partitioning into audit
  buckets (`:165-170`). The partitioning logic in particular is business rule, not transport.
- `jdparser/graph/nodes.py` (325) — nine graph nodes plus the location-override algorithm
  (`:112-142`) plus the display gate `is_qualified` (`:312-325`).

**Recommendation: do not split either.** Both are cohesive around one pipeline, both are
under 400 lines, and both are cited by name throughout `SPEC.md` and
`IMPLEMENTATION_*.md` — a split invalidates doc references for a gain that is currently
aesthetic. Recorded so a future reader knows this was evaluated and declined, not missed.
Revisit if either passes ~500 lines.

### F17 — `_WS = re.compile(r"\s+")` defined twice (won't fix)

**Category:** DRY violation. **No phase — explicitly declined.**

`extract/jsonld.py:14` and `extract/ats.py:13`. One line, zero divergence risk (the regex
has one correct form), and hoisting it creates an import between two modules that are
otherwise independent siblings. Listed for completeness because the `extract/` folder was
cross-compared file-against-file; the correct action is none.

---

## 3. Findings not searched further, and why

Per the pattern-amplification rule, each confirmed category was grepped repo-wide before
being closed. Results:

| Category | Searched | Result |
|---|---|---|
| Hardcoded assumption (branchless) | `adzuna` across all 37 files; every load-bearing domain noun | Found 2: Adzuna (F2), US-only country (F2, declined) |
| Terminology drift | `GLM\|glm-` repo-wide | Found 20 sites across 8 files (F4) |
| Diverged duplicate accessors | `display_name`, `redirect_url`, `get("id")`, `get("title")` repo-wide | Found 8 destructuring sites, 1 diverged (F1/F2) |
| Unimplemented stubs | `TODO\|FIXME\|NotImplementedError\|XXX\|HACK` across `jdparser/`, `app/`, `components/`, `lib/` | **Zero hits.** No stubs on any production path. |
| Deliberate shortcuts | `ponytail:` markers | 2, both documented with their upgrade path (`server.py:105`, `llm/screener.py:94`). Both are correct calls at MVP scale; neither is a finding. |

---

## 4. Designs

### 4.1 The `Job` domain model (Phase 2)

**Problem it solves:** F1 (root cause) and F2's shape leak. **Not** the provider seam — that
is §4.2.

Today every consumer re-destructures the provider's wire format with its own null-handling.
The fix is one normalization at the boundary and a typed model everywhere downstream:

```python
# jdparser/jobs.py  (new)
class Job(BaseModel):
    """A job posting, normalized at the source boundary. Consumers never touch raw
    provider JSON — that is what let dedupe.py drift into a crash (see REFACTOR_AUDIT F1)."""
    id: str
    title: str
    company: str
    location: str
    description: str          # the short search-result snippet, not the full JD
    redirect_url: str
    raw: dict[str, Any]       # provider payload, preserved for EvaluatedJob.source
    is_recruitment_agency: bool = False   # tagged at the relevance screen
```

`adzuna/client.py` gains `_to_job(raw) -> Job` doing the null-safe extraction **once**.
`dedupe`, `screen_jobs`, `_screened_out_entry`, `_job_meta`, and `evaluate_jobs` take `Job`.

**Why `raw` is retained rather than dropped:** `EvaluatedJob.source` (`llm/schemas.py:217`)
is persisted into every run record and is documented as heterogeneous passthrough. Dropping
it would change the on-disk shape of `data/runs/*.json`. Phase 2's domain constraint (Tier
3) is that the persisted shape is **byte-identical** before and after.

**Self-consistency check.** §2/F1 argues the null-handling is per-site and has already
diverged; this design removes the per-site handling entirely rather than adding a shared
helper each site must remember to call. Consistent.

### 4.2 The job-source seam (Phase 3)

**Problem it solves:** F2's naming and prompt coupling. **Depends on Phase 2** — the seam is
not expressible without a `Job` type to be the return value.

```python
# jdparser/jobsource/base.py  (new)
class JobSource(Protocol):
    name: str                                    # -> ErrorRecord.detail, run records
    query_model: type[BaseModel]                 # the provider's query schema
    planner_prompt: str                          # provider-specific query-language rules
    def search(self, plan: list[BaseModel]) -> list[Job]: ...
```

`jdparser/adzuna/` moves to `jdparser/jobsource/adzuna/` and becomes the sole implementation.
Renames in the provider-agnostic layers: `adzuna_results` → `job_results` (`graph/state.py:42`),
`run_adzuna_search` → `search_jobs` (`graph/nodes.py:178`, `graph/build.py:80,91`),
`plan_adzuna_queries` → `plan_queries` (`llm/search_planner.py:105`).

**`ErrorStage` is renamed and the `Literal` union keeps the old value permanently.**
`llm/schemas.py:230`'s `"adzuna_search"` is not just an in-memory enum — it is written into
every `data/runs/*.json` and re-validated on read. The union becomes:

```python
ErrorStage = Literal[
    "resume_extract", "profile", "search_plan",
    "job_search",       # current
    "adzuna_search",    # back-compat: persisted in run records written before the
                        # job-source seam (REFACTOR_AUDIT Phase 3). Never emitted by
                        # new code; removing it makes historical runs unreadable.
    "screen", "resolve", "fetch", "extract", "quality", "parse", "judge", "aggregate",
]
```

**Rationale (owner decision — do not "clean this up" later).** Dropping the dead literal
looks like obvious tidying to anyone reading only the code, because nothing writes it any
more. It is load-bearing: `runs/store.py:75` swallows validation failures with
`except: continue`, so removing the value would delete the user's entire run history from the
UI with no error, no log line, and no test failure. One line of permanent back-compat is the
price of not silently destroying data. The alternative — leaving the stage named
`"adzuna_search"` after the seam lands — was considered and rejected: it defeats the point of
renaming the layer, for the same one-line cost.

**Deliberately NOT abstracted, with rationale — do not "correct" these back:**

- `AdzunaQuery` **stays** provider-named and stays in `llm/schemas.py`. It genuinely is
  Adzuna's query language (`what`/`what_or`/`where`/`distance`); renaming it to `JobQuery`
  would claim a generality it does not have. A second provider brings its *own* query model
  behind `JobSource.query_model`.
- The planner prompt **stays a single Adzuna prompt**, moved to a file (Phase 5) and
  referenced via `JobSource.planner_prompt`. There is no meaningful "generic job-search
  planner" prompt to write — the 91 lines are entirely about Adzuna's matching semantics.
- `_apply_location_override` (`graph/nodes.py:112`) **stays where it is**, unchanged. It
  encodes Adzuna's `where`-geocoding behavior; relocating it into the source module is
  correct only once a second source proves the rule is provider-specific rather than
  universal. Doing it now is speculative.
- `ADZUNA_COUNTRY = "us"` **stays**. US-only is a stated MVP constraint (`SPEC §8`), not an
  accident, and no code path wants a second value.
- `JD_FETCH_REFERER` (`config.py:71`) **stays a constant but is renamed** to
  `HTTP_REFERER` with its comment rewritten. The value must remain `https://www.adzuna.com/`
  — `config.py:67-70` records that it was chosen empirically to get past Adzuna's `/land/`
  403s. This is the one place where a provider-specific value legitimately lives in a
  generic layer, and the rename plus comment makes that explicit instead of implicit.

**Self-consistency check.** §2/F2 identifies the prompt as the deepest coupling and the
client as the shallowest. A design that abstracted the client while leaving the prompt
unexamined would contradict that evidence — so the prompt is explicitly assigned to
`JobSource.planner_prompt` rather than left in the agnostic planner module. Consistent.

**Scope discipline.** Phase 3 introduces the seam and migrates Adzuna into it **unchanged**.
It does not add a second source. That is a later, separate piece of work outside this audit.

### 4.3 What this audit does not propose

Recorded so these read as decisions, not omissions: no split of `server.py` or `nodes.py`
(F16), no hoist of `_WS` (F17), no multi-country support (F2), no TypeScript codegen from
Pydantic (F11), and no second job source (§4.2).

---

## 5. Execution plan

> **Completed.** These rules governed the eight commits listed at the top of this document;
> they are not standing instructions. A fresh agent picking up new work should NOT check out
> `refactor/audit-938203f` or continue its numbering — cut a new branch. The verification
> commands in §5.2 remain current and are the reason this section is still worth reading.

### 5.1 Workflow rules (as executed)

- **Branch.** Work happens on `refactor/audit-938203f`, cut from `main`.
  *Idempotent:* `git rev-parse --verify refactor/audit-938203f` succeeds → `git checkout`
  it; fails → `git checkout -b refactor/audit-938203f main`. Never create a second branch,
  never execute on `main`.
- **Commits.** One commit per completed, verified phase. Message begins `Phase N: <name>`.
  **The commit log is the execution ledger** — a fresh or resumed agent determines exactly
  where the plan stands from `git log --oneline main..HEAD` alone, with no access to this
  conversation.
- **Order.** Phases run in the stated sequence. Phase 3 depends on Phase 2's `Job` model;
  Phase 7 depends on every earlier phase because it documents their results. Do not reorder.
- **Escalation.** Any design decision not already resolved in §4 → **stop and ask**. Do not
  improvise an architectural call mid-phase.
- **Scope.** A phase does what its row says and nothing adjacent. In particular Phase 3 does
  not add a second job source, and Phase 6 does not restyle the UI.

### 5.2 Verification commands per component

Looked up from `packages/api/pyproject.toml:38-46` and `packages/web/package.json:5-10`.
Never guess these; never skip verification because a command could not be found.

| Component | Command | Baseline at `938203f` |
|---|---|---|
| API — tests | `cd packages/api && uv run pytest` | `137 passed, 1 warning` |
| API — types | `cd packages/api && uv run mypy --strict jdparser` | `Success: no issues found in 36 source files` |
| Web — types | `cd packages/web && bun run typecheck` | clean |
| Web — build | `cd packages/web && bun run build` | clean |

The 1 pytest warning is a pre-existing LangGraph deprecation notice, not a regression.

### 5.3 Phases

| # | Name | Resolves | Files | Verify |
|---|---|---|---|---|
| 0 | Land the audit | F6 (partial) | `docs/REFACTOR_AUDIT.md`, delete `CLEANUP.md` | none (docs only) |
| 1 | Fix the dedupe null crash | F1 | `adzuna/dedupe.py`, `tests/test_dedupe.py` | API tests + types |
| 2 | Introduce the `Job` model | F2 (shape leak) | `jobs.py` (new), `adzuna/client.py`, `adzuna/dedupe.py`, `graph/nodes.py`, `graph/subgraph.py`, `graph/state.py`, `llm/screener.py`, tests | API tests + types |
| 3 | Job-source seam | F2 (naming, prompt) | `jobsource/` (new), `graph/*`, `llm/search_planner.py`, `llm/schemas.py`, `config.py`, tests | API tests + types |
| 4 | Test structure | F3, F13 | `tests/conftest.py` (new), split `test_graph_smoke.py`, fold in `test_location_override.py` | API tests + types |
| 5 | Prompts to files | F8, F14 | `llm/prompts/` (new), all six `llm/*.py` agents | API tests + types |
| 6 | Web DRY + type drift | F9, F10, F11, F12 | `FailuresPanel.tsx`, `page.tsx`, `QualifiedJobs.tsx`, `lib/types.ts` | Web types + build |
| 7 | Docs re-sync | F4, F5, F6, F7, F15, F16 | all `*.md`, new subpackage `README.md`s | all four commands |

### 5.4 Per-phase detail and verification tier

**Phase 0 — Land the audit.**
Commit this document at `docs/REFACTOR_AUDIT.md`. Delete `CLEANUP.md` (stale; every item
landed in `b5a7d6d`, content preserved in that commit message). Placed in `docs/` rather
than the repo root specifically so it does not worsen F6.
*Tier: none — no code changes.*

**Phase 1 — Fix the dedupe null crash.**
`adzuna/dedupe.py:18,20` → `(job.get("company") or {})` / `(job.get("location") or {})`,
matching the two already-correct copies. Add a regression test to `tests/test_dedupe.py`
asserting `dedupe([{... "company": None, "location": None ...}])` returns one row instead of
raising.
*Tier 1 (compiles) + 2 (137 tests still pass) + **3: the new test must fail against the
pre-fix `dedupe.py`.*** Confirm that before committing — a regression test that passes
either way proves nothing.

**Phase 2 — Introduce the `Job` model.** Design: §4.1.
Add `jdparser/jobs.py`. `adzuna/client.py` normalizes once via `_to_job`. Migrate the eight
destructuring sites listed in F2 to typed field access. `graph/state.py:42`'s
`adzuna_results` and `deduped_jobs` now carry `Job.model_dump()` payloads (still dicts —
LangGraph channels must stay JSON-serializable; see `graph/state.py:1-6`).
*Tier 1 + 2 + **3: `EvaluatedJob.source` must serialize byte-identically to before.*** Prove
it: capture a `data/runs/*.json` fixture's `source` object before, and assert the post-change
value round-trips equal. If no run record exists locally, build the assertion from
`tests/test_graph_smoke.py`'s `_job()` builder instead.

**Phase 3 — Job-source seam.** Design, explicit non-goals, and the `ErrorStage`
back-compat decision: §4.2.
*Tier 1 + 2 + **3: an `ErrorRecord` carrying the old `"adzuna_search"` stage must still
validate after the rename.*** Assert it directly —
`ErrorRecord.model_validate({..., "stage": "adzuna_search", ...})` must not raise, and
`list_runs()` must return a record containing one. Without that assertion the failure mode is
silent: `runs/store.py:75`'s `except: continue` drops every historical run and the run-history
panel simply shows fewer rows, with no error anywhere.

**Phase 4 — Test structure.**
Add `tests/conftest.py` with shared `profile()` / `stored_profile()` fixtures; delete the six
local copies (F3). Split `test_graph_smoke.py` (F13) into `test_graph_smoke.py` (count
invariants, real-graph failure paths), `test_display_gate.py` (`is_qualified` units),
`test_screen_jobs.py` (relevance, cap, agency). Fold `test_graph_smoke.py:448-563` into
`test_location_override.py` so one concern has one file.
*Tier 1 + 2 — and the test **count must not drop**. `uv run pytest` reporting fewer than 137
means a test was lost in the move, not consolidated. If a genuine duplicate is removed, say
so explicitly in the commit message with the count delta.*

**Phase 5 — Prompts to files.**
Move the six `_SYSTEM` blocks (F8) to `llm/prompts/*.md`, loaded via
`importlib.resources`. **`search_planner.py:102`'s runtime `%` interpolation must be
preserved** — the loaded template still formats against `ADZUNA_MAX_PAGES` and
`SEARCH_PLAN_MAX_QUERIES`. Add the prompts directory to
`pyproject.toml:37`'s wheel packaging so it ships. Fix F14 (`fit_judge.py:114` →
`notes: list[CandidateNote] | None = None`).
*Tier 1 + 2 + **3: each extracted prompt must be byte-identical to the string the old
module produced.*** Assert it — a stray trailing newline changes model behavior and no test
would catch it.

**Phase 6 — Web DRY + type drift.**
`FailuresPanel.tsx`: one `<ScreenedList>` component replacing the three copies (F9).
`page.tsx`: one `resetForNewRun()` helper; **preserve each handler's existing divergence**
(`handleOpenRun` keeps the prior run visible, `handleParse` sets `"idle"`) via explicit
arguments rather than flattening them — F10 notes the divergence is partly intentional and
this phase must not silently pick one behavior. `QualifiedJobs.tsx:9`: drop the client-side
re-sort, keep the server order, and correct the comment at `:4-6` (F12). `lib/types.ts:46`:
add the four missing `StoredResumeProfile` fields (F11).
*Tier 1 (`bun run typecheck`) + **3: `bun run build` must succeed** — there is no web test
suite, so the build is the only regression signal. State that limitation in the commit
message rather than implying test coverage.*

**Phase 7 — Docs re-sync.**
Rename GLM → Gemini across all 20 sites in F4, including the self-contradicting
`SPEC.md:940`, the third-version `SPEC.md:790`, and the dead `MODEL_GLM` symbol at
`IMPLEMENTATION_GRAPH.md:53`. Document in `config.py:91-94` that `MODEL_LOGIC` and
`MODEL_GEMINI_FLASH_LITE` currently resolve to the same slug and why the split constant is
kept. Add the five missing constants and three missing features to `SPEC §6` (F5). Fix "four
LLM agents" → six (`llm/client.py:3`, `IMPLEMENTATION_LLM.md:1`). Replace the duplicated
`is_qualified` body at `SPEC.md:876-891` with a pointer to `graph/nodes.py` plus the
normative *conditions* in prose (F15) — the spec should state the contract, not mirror the
implementation. Add a short README to each of the seven subpackages: one sentence of purpose,
one line per module, a backlink to the owning `IMPLEMENTATION_*.md` rather than restating it
(F7). Record F16's decline in `IMPLEMENTATION_API.md` / `IMPLEMENTATION_GRAPH.md`.

**Doc consolidation (F6):** move all eight `IMPLEMENTATION*.md` files into `docs/`, leaving
`README.md`, `PLAN.md`, `SPEC.md`, and `BUILD.md` at the repo root. This halves the root
listing while keeping the four docs a newcomer actually opens first exactly where they look
for them. Every cross-reference between the moved files, and every reference to them from the
four that stay, must be re-pointed — `grep -rn "IMPLEMENTATION" *.md docs/` after the move
should show no path that does not resolve.

**Also update `PLAN.md:47` (consequence of Phase 3).** It currently lists "Job sources other
than Adzuna" under *Out of scope (MVP)*. After the seam lands that line is half-true and
misleading in both directions: the abstraction exists, but Adzuna is still the only
implementation. Reword to say the seam is in place and a second source is not yet
implemented, so the next reader neither believes multi-source works nor believes the seam
needs building.
*Tier 1 + 2 across **all four commands** — Phase 7 touches `config.py` comments and
`llm/client.py`'s docstring, so it is not docs-only.*

---

## 6. Findings ↔ phases (both directions)

Every finding maps to a phase or an explicit decline; every phase names what it resolves.

| Finding | Phase | | Phase | Resolves |
|---|---|---|---|---|
| F1 dedupe crash | 1 | | 0 | F6 (partial) |
| F2 Adzuna hardcoding | 2, 3 | | 1 | F1 |
| F3 test fixtures | 4 | | 2 | F2 (shape) |
| F4 GLM drift | 7 | | 3 | F2 (naming, prompt) |
| F5 SPEC staleness | 7 | | 4 | F3, F13 |
| F6 doc sprawl | 0, 7 | | 5 | F8, F14 |
| F7 no subpackage READMEs | 7 | | 6 | F9, F10, F11, F12 |
| F8 embedded prompts | 5 | | 7 | F4, F5, F6, F15, F16 |
| F9 FailuresPanel triplication | 6 | | — | F17 (declined) |
| F10 page.tsx preamble | 6 | | | |
| F11 types.ts drift | 6 | | | |
| F12 agency ordering | 6 | | | |
| F13 test_graph_smoke.py | 4 | | | |
| F14 mutable default | 5 | | | |
| F15 SPEC duplicates the gate | 7 | | | |
| F16 server.py / nodes.py | 7 (documented, **not split**) | | | |
| F17 duplicate `_WS` | **won't fix** (§2/F17) | | | |

All eight phases are approved and in scope. There are no unresolved decisions — anything
encountered during execution that §4 does not already settle is an escalation (§5.1), not a
judgment call.
