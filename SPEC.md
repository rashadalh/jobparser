# SPEC — Resume-Driven Job Matching (system contract)

> Canonical contract. Every implementation doc references this by section number.
> Tone: precise, terse. Code blocks here are **ground truth** — if the
> implementation diverges, update this doc, not the other way around.
>
> **This document supersedes `docs/system-overview.html`** wherever they differ.
> The overview's TypeScript `StoredResumeProfile` is illustrative; §3.6 below is
> the canonical (snake_case) record.

---

## 1. System overview

Pipeline (see `PLAN.md` for prose, `docs/system-overview.html` for the diagram):

```
upload ─▶ extract_resume_text ─▶ fingerprint_resume ─▶ load_or_parse_profile
        ─▶ plan_searches ─▶ search_jobs ─▶ dedupe_jobs
        ─▶ evaluate_jobs (fan-out N workers) ─▶ aggregate_matches ─▶ results
```

Each fan-out worker runs the **job-evaluation subgraph** (§4.3):
`resolve_url → fetch_page → extract_jd → parse_requirements → judge_fit`,
short-circuiting to `record_failure` on any stage failure.

Two LLM tiers (§3, §6.3), both low-cost open-weight models via **OpenRouter**:
**logic** work (resume profiling, search planning, fit judgment) runs on **gemini-3.1-flash-lite**;
**text extraction** (turning JD prose into structured requirements) runs on
**Gemini 3.1 Flash Lite**.

Three persistence layers, all JSON flat-files (no DB):
- **Resume-profile cache** — durable, content-addressed; survives across runs (§3.6, §3.7).
- **Run records** — per browser-initiated run; status + results for polling (§3.9).
- **LangGraph checkpointer** — in-memory (MVP); resumes a single graph run only.

---

## 2. Repo layout

Every file an agent may create appears here.

```
jdparser/
├── PLAN.md  SPEC.md  docs/IMPLEMENTATION.md  BUILD.md  README.md
├── docs/IMPLEMENTATION_GRAPH.md  docs/IMPLEMENTATION_LLM.md  docs/IMPLEMENTATION_ADZUNA.md
├── docs/IMPLEMENTATION_EXTRACT.md  docs/IMPLEMENTATION_CACHE.md  docs/IMPLEMENTATION_API.md
├── docs/IMPLEMENTATION_WEB.md
├── .env.example                       # committed; .env (api) is gitignored
├── docs/
│   └── system-overview.html
├── packages/
│   ├── api/
│   │   ├── pyproject.toml             # PEP 621 deps + build backend; managed by uv
│   │   ├── uv.lock                    # uv lockfile (committed)
│   │   ├── .python-version            # "3.11" — uv selects the interpreter
│   │   ├── jdparser/
│   │   │   ├── __init__.py
│   │   │   ├── __main__.py            # CLI entry: `uv run python -m jdparser <resume>`
│   │   │   ├── config.py              # settings, env, constants (§6)
│   │   │   ├── server.py              # FastAPI app (§5)
│   │   │   ├── graph/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── state.py           # JobMatchState, JobEvalState (§3.1, §3.2)
│   │   │   │   ├── build.py           # graph + subgraph assembly
│   │   │   │   ├── nodes.py           # top-level node fns (§4.1)
│   │   │   │   └── subgraph.py        # evaluation subgraph (§4.3)
│   │   │   ├── llm/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── client.py          # OpenRouter (OpenAI SDK + instructor) + routing (§6.3)
│   │   │   │   ├── schemas.py         # ALL Pydantic models (§3.3–§3.10, §5.2)
│   │   │   │   ├── resume_profiler.py # profile_resume()
│   │   │   │   ├── search_planner.py  # plan_queries()
│   │   │   │   ├── jd_parser.py       # parse_jd_requirements()
│   │   │   │   └── fit_judge.py       # judge_fit()
│   │   │   ├── adzuna/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── client.py          # search(), run_search_plan()
│   │   │   │   └── dedupe.py          # dedupe()
│   │   │   ├── extract/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── resolve.py         # resolve_final_url()
│   │   │   │   ├── fetch.py           # fetch()  (httpx + Playwright)
│   │   │   │   ├── jsonld.py          # jobposting_jsonld()
│   │   │   │   ├── ats.py             # ats_extract()
│   │   │   │   ├── readable.py        # readable_text()
│   │   │   │   └── quality.py         # check_quality()
│   │   │   ├── cache/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── fingerprint.py     # compute_fingerprint()
│   │   │   │   └── store.py           # get_profile(), put_profile()
│   │   │   ├── resume/
│   │   │   │   ├── __init__.py
│   │   │   │   └── extract_text.py    # extract_text()
│   │   │   └── runs/
│   │   │       ├── __init__.py
│   │   │       └── store.py           # create_run(), get_run(), update_run()
│   │   ├── data/                      # gitignored except .gitkeep
│   │   │   ├── profiles/              # resume cache: {cache_key}.json
│   │   │   ├── runs/                  # run records: {run_id}.json
│   │   │   └── uploads/               # saved resume uploads: {run_id}.{ext}
│   │   └── tests/
│   │       ├── conftest.py
│   │       ├── fixtures/              # sample resume(s), recorded HTML/JSON
│   │       ├── test_fingerprint.py
│   │       ├── test_cache_store.py
│   │       ├── test_dedupe.py
│   │       ├── test_extract.py
│   │       ├── test_quality.py
│   │       ├── test_llm_schemas.py
│   │       ├── test_graph_smoke.py
│   │       └── test_api.py
│   └── web/
│       ├── package.json               # managed by bun
│       ├── bun.lock                   # bun lockfile (committed)
│       ├── .env.local                 # gitignored; NEXT_PUBLIC_API_BASE
│       ├── next.config.ts
│       ├── tsconfig.json
│       ├── postcss.config.mjs
│       ├── app/
│       │   ├── globals.css            # `@import "tailwindcss";`  (Tailwind v4)
│       │   ├── layout.tsx
│       │   └── page.tsx               # upload + results (client component)
│       ├── components/
│       │   ├── ResumeUpload.tsx
│       │   ├── RunStatus.tsx
│       │   ├── JobCard.tsx
│       │   └── FailuresPanel.tsx
│       └── lib/
│           ├── api.ts                 # fetch wrappers
│           └── types.ts               # mirrors §3 API JSON (snake_case)
```

---

## 3. Data types

All Python models are **Pydantic v2** (`llm/schemas.py`) unless noted. JSON on the
wire and on disk is **snake_case everywhere** (locked decision — see §10, item 1).
LangGraph state channels are `TypedDict` (untyped `dict` payloads at the channel
boundary; payloads conform to the Pydantic models, serialized via `.model_dump()`).

### 3.1 `JobMatchState` (top-level graph state) — `graph/state.py`

```python
import operator
from typing import Annotated, TypedDict


class JobMatchState(TypedDict):
    # --- run identity / inputs ---
    run_id: str
    user_id: str
    resume_file_path: str

    # --- resume → profile ---
    resume_text: str | None
    resume_fingerprint: str | None        # == cache_key (§3.7)
    resume_profile: dict | None           # ResumeProfile.model_dump()
    resume_cache_hit: bool
    candidate_notes: list[dict]           # list[CandidateNote.model_dump()] (§7.7)

    # --- discovery ---
    search_locations: list[str] | None    # per-run location override (None = profile's inferred)
    broaden_search: bool                  # False = strict locations (drop the nationwide query)
    max_days_old: int | None              # per-run listing-age cap in days (None/<=0 = any)
    include_agencies: bool                # True = let agency listings past the screen (§7.7)
    search_plan: list[dict] | None        # list[AdzunaQuery.model_dump()]
    job_results: list[dict]               # list[Job.model_dump()] — normalized at the source
    deduped_jobs: list[dict]              # deduped + relevance-screened Job.model_dump()s
    screened_out: list[dict]              # jobs dropped before evaluation, with reason (§7.7)

    # --- evaluation (fan-out reducers; see §3.10 for lifecycle) ---
    evaluated_jobs: Annotated[list[dict], operator.add]   # list[EvaluatedJob]
    qualified_jobs: list[dict]            # filtered subset of evaluated_jobs
    errors: Annotated[list[dict], operator.add]           # list[ErrorRecord]
```

`evaluated_jobs` and `errors` use `operator.add` so concurrent fan-out workers
**append** (never overwrite). See §3.10 for entry/exit/ordering rules — this is the
#1 lifecycle hazard.

### 3.2 `JobEvalState` (per-worker subgraph state) — `graph/state.py`

```python
class JobEvalState(TypedDict):
    job: dict                  # one deduped Adzuna job dict (§3.8.1)
    profile: dict              # ResumeProfile.model_dump() — injected into each Send payload
    final_url: str | None
    fetched: dict | None       # FetchResult (§3.8.2)
    jd_text: str | None
    jd_quality: dict | None    # QualityResult (§3.8.3)
    requirements: dict | None  # JobRequirements.model_dump()
    judgment: dict | None      # FitJudgment.model_dump()
    result: list[dict]         # failure-marker channel (see below)
```

`profile` is the candidate profile copied into every fan-out `Send` payload so the
per-job worker is self-contained (the judge needs it). `evaluate_jobs` (§4.1) sets it.

`result` is an **in-subgraph failure marker**, not the parent emission. When a stage
fails it sets `result = [<failure-marker dict>]`; a router sees the non-empty `result`
and routes to `record_failure`. The marker dict is an `EvaluatedJob` payload (§3.9,
`status="failed"`) plus two transient keys `_code` / `_msg` (the error code + message)
that `record_failure` reads and then **drops** before emitting the final EvaluatedJob.
The two terminal nodes (`finalize` on success, `record_failure` on failure) each build
a clean `EvaluatedJob` and return `{"evaluated_jobs": [ej]}` into the parent reducer.
`result` is never the thing emitted to the parent.

### 3.3 `ResumeProfile` (logic — gemini-3.1-flash-lite)

```python
from typing import Literal
from pydantic import BaseModel, Field

Seniority = Literal["intern", "junior", "mid", "senior", "staff", "principal", "executive"]
RemotePref = Literal["onsite", "hybrid", "remote", "any"]
EmploymentType = Literal["full_time", "part_time", "contract", "permanent"]


class ResumeEvidence(BaseModel):
    claim: str           # e.g. "5 years building distributed payment systems"
    source_quote: str    # verbatim span from the resume supporting `claim`


class WorkPeriod(BaseModel):
    title: str
    organization: str
    start_year: float            # decimal year: Jan 2018 -> 2018.0, Jul 2020 -> 2020.5
    end_year: float | None = None  # null == ongoing ("Present")


class ResumeProfile(BaseModel):
    roles: list[str]                       # normalized target roles + synonyms welcome
    skills: list[str]
    seniority: Seniority
    total_years_experience: float          # COMPUTED in code from work_periods (interval union); not LLM-estimated
    work_periods: list[WorkPeriod]         # dated roles; jdparser/experience.py computes the years total
    education: list[str]                    # degrees/credentials, e.g. ["M.S. Computer Science", "B.S. ..."]
    domains: list[str]                     # e.g. ["fintech", "healthcare"]
    work_authorization: list[str]          # free-form hints, e.g. ["us_citizen", "needs_sponsorship"]
    locations: list[str]                   # preferred locations (human-readable)
    remote_preference: RemotePref
    employment_types: list[EmploymentType]
    evidence: list[ResumeEvidence]
```

Invariant: every non-trivial claim the profiler asserts (seniority, years, a key
skill) SHOULD have a corresponding `evidence` entry. Serialization: `.model_dump()`.

### 3.4 `AdzunaQuery` (logic — gemini-3.1-flash-lite) — **closed schema**

The planner MAY expand role synonyms but **MUST NOT invent parameters**. The
schema enumerates exactly the supported Adzuna params (§3.8.1 cites the API).

```python
class AdzunaQuery(BaseModel):
    what: str                      # free-text query (Adzuna `what`)
    what_exclude: str | None = None
    where: str | None = None       # location (Adzuna `where`)
    distance: int | None = None    # km radius around `where` (Adzuna `distance`)
    max_days_old: int | None = None
    category: str | None = None    # Adzuna category tag, e.g. "it-jobs"
    salary_min: int | None = None  # annual minimum, local currency (USD for country="us")
    full_time: bool | None = None
    part_time: bool | None = None
    contract: bool | None = None
    permanent: bool | None = None
    results_per_page: int = Field(default=ADZUNA_DEFAULT_RESULTS_PER_PAGE,
                                  ge=1, le=ADZUNA_MAX_RESULTS_PER_PAGE)   # §6.1
    pages: int = Field(default=2, ge=1, le=ADZUNA_MAX_PAGES)             # §6.1
```

`ADZUNA_DEFAULT_RESULTS_PER_PAGE` (20) and `ADZUNA_MAX_RESULTS_PER_PAGE` (50, Adzuna's
hard max) are the single source of truth (§6.1) — the schema imports them; do **not**
re-hardcode `20`/`50`.

Validation: `AdzunaQuery` is validated (Pydantic) **before** any API call. A plan
is `list[AdzunaQuery]`; the planner returns ≤ `SEARCH_PLAN_MAX_QUERIES` (§6.1).

### 3.5 `JobRequirements` (text extraction — Gemini 3.1 Flash Lite)

```python
class JobRequirements(BaseModel):
    required_skills: list[str]
    preferred_skills: list[str]
    min_years_experience: float | None
    education: list[str]                   # e.g. ["BS Computer Science"]
    education_required: bool
    location_constraints: list[str]
    remote_allowed: bool | None
    responsibilities: list[str]
    dealbreakers: list[str]                # explicit hard filters, e.g. "active TS/SCI clearance"
    employment_type: EmploymentType | None
```

### 3.6 `FitJudgment` (logic — gemini-3.1-flash-lite)

```python
FitDecision = Literal["qualified", "not_qualified", "uncertain"]


class MetRequirement(BaseModel):
    requirement: str
    evidence_quote: str    # verbatim resume span proving it (cross-refs ResumeProfile.evidence)


class FitJudgment(BaseModel):
    # declared before `decision` so structured-output field order forces the model to
    # commit to these BEFORE computing the final decision (see fit_judge.py THEMATIC FIT)
    thematic_fit: bool               # same profession/specialization as the candidate's target roles/domains?
    relevant_years_experience: float # years from work_periods actually IN the JD's specialization
    #                                  (NOT profile.total_years_experience — that's a domain-blind career total)
    thematic_rationale: str          # which work_periods/skills were counted, and why the specialization does/doesn't match
    decision: FitDecision
    confidence: float = Field(ge=0.0, le=1.0)
    met_requirements: list[MetRequirement]
    missing_hard_requirements: list[str]
    failed_dealbreakers: list[str]
    rationale: str
```

Invariant: if `decision == "qualified"`, every entry in `JobRequirements.required_skills`
the judge counts as met MUST appear in `met_requirements` with a non-empty
`evidence_quote`; `failed_dealbreakers` MUST be empty; `thematic_fit` MUST be true.

### 3.7 `StoredResumeProfile` (durable cache record — JSON file)

Canonical (snake_case) — **supersedes** the camelCase TS shape in the overview.

```python
class StoredResumeProfile(BaseModel):
    id: str                  # uuid4
    user_id: str
    cache_key: str           # == resume_fingerprint         (§3.8.4)
    profile: ResumeProfile
    parser_version: str      # PARSER_VERSION  (§6.1)
    schema_version: str      # SCHEMA_VERSION  (§6.1)
    model: str               # OpenRouter slug that produced `profile` (e.g. "google/gemini-3.1-flash-lite")
    created_at: str          # ISO-8601 UTC
    updated_at: str          # ISO-8601 UTC
```

Stored at `packages/api/data/profiles/{cache_key}.json`. See §3.7-lifecycle below.

### 3.8 Helper / boundary types

#### 3.8.1 Raw Adzuna job dict (subset we depend on)

Adzuna `GET /v1/api/jobs/{country}/search/{page}` returns `{"results": [...]}`.
We persist and depend on (other keys passed through untouched):

```jsonc
{
  "id": "1234567890",                       // string id
  "title": "Senior Backend Engineer",
  "company": { "display_name": "Acme Inc" },
  "location": { "display_name": "Austin, TX", "area": ["US", "Texas", "Austin"] },
  "redirect_url": "https://www.adzuna.com/land/ad/1234567890?...",  // ALWAYS a redirect
  "created": "2026-06-20T12:00:00Z",
  "description": "Truncated snippet from Adzuna (NOT the full JD)",
  "contract_time": "full_time",             // optional
  "category": { "tag": "it-jobs", "label": "IT Jobs" }
}
```

`redirect_url` is an Adzuna redirect, **never** the employer page. §4.3 resolves it.
`description` is a snippet — it is **not** sufficient evidence; the full JD must be
extracted (§4.3, qualification gate §7-rule-1).

#### 3.8.2 `FetchResult` — `extract/fetch.py`

```python
class FetchResult(BaseModel):
    url: str
    status: int
    html: str
    source: Literal["http", "playwright"]   # which path produced `html`
```

#### 3.8.3 `QualityResult` — `extract/quality.py`

```python
class QualityResult(BaseModel):
    char_len: int
    passed: bool
    reasons: list[str]    # machine codes when failed, e.g. ["JD_TOO_SHORT"]
```

#### 3.8.4 `Fingerprint` — `cache/fingerprint.py`

```python
class Fingerprint(BaseModel):
    # Only cache_key is persisted. file_hash/text_hash were written but never read —
    # lookup has always been by cache_key alone — and file_hash forced a full-file
    # read_bytes() purely to store an unused value. Removed in b5a7d6d.
    cache_key: str    # sha256(f"{text_hash}:{PARSER_VERSION}:{SCHEMA_VERSION}"), hex
```

`cache_key` deliberately incorporates parser/schema versions so a version bump
invalidates the cache. It does **not** include `model` (the overview specifies
"cached by text hash and parser version"); the producing model is recorded in
`StoredResumeProfile.model` for audit only.

### 3.9 `EvaluatedJob` (per-job result; appended to `evaluated_jobs`)

```python
JobStatus = Literal["qualified", "not_qualified", "uncertain", "failed"]
FailureStage = Literal["resolve", "fetch", "extract", "quality", "parse", "judge"]


class EvaluatedJob(BaseModel):
    job_id: str                      # Adzuna id (or dedupe surrogate)
    title: str
    company: str
    location: str
    final_url: str | None
    source: dict                     # the raw Adzuna job dict (§3.8.1)
    jd_char_len: int | None
    requirements: JobRequirements | None
    judgment: FitJudgment | None
    status: JobStatus
    failure_stage: FailureStage | None   # set iff status == "failed"
```

`status` is derived by deterministic code in the subgraph terminal nodes
(`finalize`/`record_failure`, §4.3), **never** by the LLM. `aggregate_matches`
(§4.1) only *filters* — it does not set `status` (the `evaluated_jobs` reducer is
append-only and cannot be mutated downstream). Derivation (single source of truth):

| Condition | `status` |
|---|---|
| any stage failed | `failed` (+ `failure_stage`) |
| `judgment.decision == "qualified"` AND `confidence ≥ CONFIDENCE_THRESHOLD` | `qualified` |
| `judgment.decision == "qualified"` AND `confidence < CONFIDENCE_THRESHOLD` | `uncertain` |
| `judgment.decision == "uncertain"` | `uncertain` |
| `judgment.decision == "not_qualified"` | `not_qualified` |

`failure_stage` is non-null **iff** `status == "failed"`. A qualified-but-not-confident
job is recorded as `uncertain` (it lands in `rejected`, never the feed).

### 3.10 `ErrorRecord` (appended to `errors`)

```python
ErrorStage = Literal[
    "resume_extract", "profile", "search_plan", "adzuna_search",
    "resolve", "fetch", "extract", "quality", "parse", "judge", "aggregate",
]
# Note: `dedupe` is pure and raises nothing — deliberately not an ErrorStage.


class ErrorRecord(BaseModel):
    job_id: str | None        # null for non-per-job stages
    stage: ErrorStage
    code: str                 # machine code (§6.4)
    message: str
    detail: dict | None = None
```

---

## Mutable shared state — lifecycle (normative)

> Per the method, every mutable shared state declares **entry**, **exit**, and
> **in-place-mutation** rules, plus which consumer's invariants depend on timing.

### `evaluated_jobs` (reducer, `operator.add`)
- **Enters:** exactly once per deduped job, when that job's subgraph reaches its
  terminal node (`judge_fit` success OR `record_failure`). Each worker returns
  `{"evaluated_jobs": [one EvaluatedJob]}`.
- **Exits:** never during a run. Read by `aggregate_matches` **after the fan-out
  join** (LangGraph blocks `aggregate_matches` until all `Send` branches finish).
- **In-place mutation:** forbidden. Workers only append via the reducer; no worker
  reads or edits another worker's entry.
- **Ordering:** nondeterministic (workers finish in any order). Consumers MUST NOT
  assume `evaluated_jobs[i]` corresponds to `deduped_jobs[i]`. Match by `job_id`.
- **Timing invariant:** `len(evaluated_jobs) == len(deduped_jobs)` at
  `aggregate_matches` entry. If not, that's a join bug — `aggregate_matches`
  **raises** `JDParserError(code="EVAL_COUNT_MISMATCH")`, which propagates out of
  `graph.invoke` and the API runner records the run as `failed` (§5, IMPLEMENTATION_API
  `_execute`). It does **not** complete with empty results.

### `errors` (reducer, `operator.add`)
- **Enters:** any node that catches a recoverable error appends one `ErrorRecord`.
  Per-job failures append here **and** still emit a `failed` EvaluatedJob.
- **Exits:** never during a run. **Consumer:** the API runner copies `final["errors"]`
  into `RunRecord.errors` (§5.2); the audit UI renders it alongside per-job failures.
  **Push/pull note:** there is no push channel; the run record (pull) is the single
  source of truth (§5, §10 item 6).
- **In-place mutation:** forbidden (append-only).

### LangGraph checkpointer (`MemorySaver`, in-memory)
- **Enters:** the graph is compiled once with `MemorySaver()`; each run writes state
  under `thread_id == run_id` (set in the invoke config).
- **Exits:** **never** in MVP — there is no eviction. State for every completed run
  stays in process memory until restart. This is an accepted MVP limitation (§9): a
  long-lived single-process server grows unbounded; a persistent/evicting checkpointer
  is the eventual form.
- **In-place mutation:** owned by LangGraph (per-superstep snapshots); application code
  never mutates checkpoints directly.

### Resume-profile cache (`data/profiles/{cache_key}.json`)
- **Enters:** on a cache **miss**, after the profiler LLM returns a valid
  `ResumeProfile`, `put_profile()` writes the file (create or overwrite same key).
- **Exits:** only by explicit deletion or a `cache_key` change (parser/schema bump
  ⇒ new key ⇒ old file orphaned, harmless). Never auto-evicted.
- **In-place mutation:** a write to an existing `cache_key` overwrites atomically
  (write temp file, `os.replace`). `put_profile()` itself preserves `created_at` —
  if a file already exists at the key it reads the existing `created_at` and keeps it,
  refreshing only `updated_at`. The caller does not manage this.
- **Consumer timing:** `load_or_parse_profile` (§4.1) reads before deciding to call
  the LLM; a hit sets `resume_cache_hit=True` and skips the profiler call entirely.

### Run record (`data/runs/{run_id}.json`)
- **Enters:** `create_run()` at `POST /api/runs` with `status="pending"`.
- **Mutates:** background task sets `running`, then terminal `completed`/`failed`
  with results. Each write is atomic (temp + `os.replace`).
- **Exits:** never auto-deleted in MVP.
- **Consumer timing:** the frontend polls `GET /api/runs/{run_id}` until
  `status ∈ {completed, failed}` (terminal). See §5.

---

## 4. Interfaces

`function name(arg: T) -> Ret  // semantics`. These signatures are canonical;
area docs keep them byte-identical.

### 4.1 Top-level graph nodes — `graph/nodes.py`

Each takes the full state, returns a **partial** state dict (LangGraph merges).

```python
def extract_resume_text(state: JobMatchState) -> dict
    # -> {"resume_text": <normalized>} ; on failure append ErrorRecord(stage="resume_extract") and raise

def fingerprint_resume(state: JobMatchState) -> dict
    # -> {"resume_fingerprint": cache_key}  (only cache_key in state; full Fingerprint
    #    is recomputed in load_or_parse_profile on a cache miss)

def load_or_parse_profile(state: JobMatchState) -> dict
    # cache hit  -> {"resume_profile": p, "resume_cache_hit": True, "candidate_notes": [...]}
    # cache miss -> calls profile_resume(); put_profile(); returns same shape with cache_hit False

def plan_searches(state: JobMatchState) -> dict
    # -> {"search_plan": [AdzunaQuery.model_dump(), ...]}

def search_jobs(state: JobMatchState) -> dict
    # -> {"job_results": [raw job dict, ...]}

def dedupe_jobs(state: JobMatchState) -> dict
    # -> {"deduped_jobs": [raw job dict, ...]}

def evaluate_jobs(state: JobMatchState) -> list[Send]
    # fan-out (conditional-edge fn). Injects the profile into each worker payload:
    #   return [Send("job_eval",
    #                {"job": j, "profile": state["resume_profile"], "result": []})
    #           for j in state["deduped_jobs"]]

def aggregate_matches(state: JobMatchState) -> dict
    # if len(evaluated_jobs) != len(deduped_jobs): raise JDParserError(EVAL_COUNT_MISMATCH) (§3.10)
    # else -> {"qualified_jobs": [ej for ej in evaluated_jobs if is_qualified(ej)]}  (§7)
```

### 4.2 Graph assembly — `graph/build.py`

```python
def build_graph() -> CompiledStateGraph   # top-level graph, subgraph compiled in (§4.3)
def build_subgraph() -> CompiledStateGraph # the job-evaluation subgraph
```

### 4.3 Job-evaluation subgraph nodes — `graph/subgraph.py`

```python
def resolve_url(s: JobEvalState) -> dict       # -> {"final_url": url} | failure
def fetch_page(s: JobEvalState) -> dict        # -> {"fetched": FetchResult.model_dump()} | failure
def extract_jd(s: JobEvalState) -> dict        # -> {"jd_text": str} | failure
def check_jd(s: JobEvalState) -> dict          # -> {"jd_quality": QualityResult.model_dump()}
def parse_requirements(s: JobEvalState) -> dict # -> {"requirements": JobRequirements.model_dump()} | failure
def judge_fit_node(s: JobEvalState) -> dict     # -> {"judgment": FitJudgment.model_dump()} | failure
def finalize(s: JobEvalState) -> dict          # -> {"evaluated_jobs": [EvaluatedJob.model_dump()]}
def record_failure(s: JobEvalState) -> dict    # -> {"evaluated_jobs": [failed EvaluatedJob], "errors":[..]}
```

Edges: each stage routes to the next on success, or to `record_failure` on failure
(failure = the stage returned/raised a handled error). `check_jd` routes to
`parse_requirements` if `passed` else `record_failure(stage="quality")`. Both
`finalize` and `record_failure` are terminal subgraph nodes that emit into the
parent `evaluated_jobs` reducer (§3.10).

**Complete subgraph mapping (parallel-enum table — all 6 stages, do not infer):**

| Subgraph node | `EvaluatedJob.failure_stage` | `ErrorRecord.code(s)` (§6.4) |
|---|---|---|
| `resolve_url` | `resolve` | `RESOLVE_FAILED` |
| `fetch_page` | `fetch` | `FETCH_FAILED` |
| `extract_jd` | `extract` | `JD_NOT_FOUND` |
| `check_jd` | `quality` | `JD_TOO_SHORT`, `JD_NOISY` |
| `parse_requirements` | `parse` | `PARSE_INVALID` |
| `judge_fit` | `judge` | `JUDGE_INVALID` |

> Naming note: subgraph **node names** (`resolve_url`, `fetch_page`, `extract_jd`,
> `check_jd`, `parse_requirements`, `judge_fit`) intentionally differ from the
> `failure_stage` enum values above; this table is the authoritative mapping. The
> LangGraph node registered as `"judge_fit"` runs the `judge_fit_node` function,
> which calls the `judge_fit()` LLM agent (§4.4) — the name reuse is intentional and
> namespaced (node name vs. function vs. agent).

### 4.4 LLM agents — `llm/`

```python
def profile_resume(resume_text: str) -> ResumeProfile          # resume_profiler.py  (logic)
def plan_queries(profile: ResumeProfile) -> list[AdzunaQuery]  # search_planner.py (logic)
def parse_jd_requirements(jd_text: str) -> JobRequirements      # jd_parser.py       (Gemini Flash Lite)
def judge_fit(profile: ResumeProfile, requirements: JobRequirements) -> FitJudgment  # fit_judge.py (logic)
```

All four call `instructor`'s `client.chat.completions.create(model=...,
response_model=<Schema>, max_retries=LLM_MAX_RETRIES, ...)` over the OpenAI SDK
pointed at OpenRouter, and return the validated Pydantic object (§ IMPLEMENTATION_LLM).
Model slug per §6.3.

### 4.5 Adzuna — `adzuna/`

```python
def search(query: AdzunaQuery, page: int) -> list[dict]   # one API page → raw job dicts
def run_search_plan(plan: list[AdzunaQuery]) -> list[dict] # all queries × pages, flattened
def dedupe(jobs: list[dict]) -> list[dict]                 # dedupe.py (§ rule below)
```

`dedupe` key = `(final_url or redirect_url, company.display_name, title, location.display_name)`
normalized (lowercased, stripped). First occurrence wins.

### 4.6 Extraction — `extract/`

```python
def resolve_final_url(redirect_url: str) -> str            # resolve.py — follow redirects, return final URL
def fetch(url: str) -> FetchResult                          # fetch.py — httpx; Playwright fallback (§ EXTRACT)
def jobposting_jsonld(html: str) -> str | None             # jsonld.py — JobPosting schema.org description
def ats_extract(html: str, url: str) -> str | None         # ats.py — Greenhouse/Lever/etc. selectors
def readable_text(html: str) -> str | None                 # readable.py — trafilatura main-content
def extract_jd_text(fetched: FetchResult) -> str | None    # orchestrates jsonld → ats → readable
def check_quality(text: str) -> QualityResult              # quality.py (§6.1 thresholds)
```

### 4.7 Cache — `cache/`, `resume/`

```python
def extract_text(file_path: str) -> str                    # resume/extract_text.py (pdf/docx/txt → normalized)
def compute_fingerprint(file_path: str, text: str) -> Fingerprint   # cache/fingerprint.py
def get_profile(cache_key: str) -> StoredResumeProfile | None       # cache/store.py
def put_profile(record: StoredResumeProfile) -> None                # cache/store.py (atomic write)
```

### 4.8 Runs — `runs/store.py`

```python
def create_run(run_id: str, user_id: str, resume_file_path: str) -> RunRecord
    # run_id is generated by the caller BEFORE the upload is saved, so the upload
    # file can be named data/uploads/{run_id}.{ext}
def get_run(run_id: str) -> RunRecord | None
def update_run(run_id: str, **fields) -> RunRecord       # atomic merge-write
```

`RunRecord` (Pydantic, §5.2).

---

## 5. API (REST) — `server.py`

Base path `/api`. All request/response bodies snake_case JSON.

### 5.1 Endpoints

```
POST /api/runs
  body: multipart/form-data { file: <resume PDF/DOCX/txt> }
  202 -> { "run_id": str, "status": "pending" }
  starts graph execution in a background task; returns immediately.

GET /api/runs/{run_id}
  200 -> RunRecord (§5.2)
  404 -> { "detail": "run not found" }

GET /api/health
  200 -> { "status": "ok" }
```

### 5.2 `RunRecord` (response shape; stored at `data/runs/{run_id}.json`)

```python
RunStatus = Literal["pending", "running", "completed", "failed"]


class RunRecord(BaseModel):
    run_id: str
    user_id: str
    status: RunStatus
    created_at: str            # ISO-8601 UTC
    updated_at: str
    resume_cache_hit: bool | None       # null until profile stage runs
    qualified_jobs: list[dict]          # list[EvaluatedJob] passing §7 gates (status=="qualified")
    failures: list[dict]                # list[EvaluatedJob] with status=="failed"  (audit view)
    rejected: list[dict]                # list[EvaluatedJob] status in {not_qualified, uncertain}
    errors: list[dict]                  # list[ErrorRecord] — non-per-job + per-job errors (audit)
    error: str | None                   # run-level FATAL error message (run failed), else null
```

Polling contract (push/pull, §10 item 6): the frontend polls every
`POLL_INTERVAL_MS` (§6.2) until `status ∈ {completed, failed}`. The run record is
the **single source of truth**; there is no push/streaming channel in MVP.

---

## 6. Constants (single source of truth)

> Every literal whose meaning depends on context is annotated with a
> context-independent unit column.

### 6.1 Pipeline constants — `config.py`

| Name | Value | Unit / real-world meaning |
|---|---|---|
| `CONFIDENCE_THRESHOLD` | `0.75` | fraction in [0,1]; min `FitJudgment.confidence` to display |
| `MIN_RESUME_CHARS` | `200` | characters; min normalized resume text (else `RESUME_EMPTY_TEXT`) |
| `MAX_RESUME_CHARS` | `40000` | characters; truncate resume text before profiler LLM |
| `MIN_JD_CHARS` | `600` | characters; min extracted JD text length to pass quality |
| `MAX_JD_CHARS` | `60000` | characters; truncate JD before LLM (cost guard) |
| `JD_BOILERPLATE_MAX_RATIO` | `0.40` | fraction; max nav/boilerplate share before quality fail |
| `BOILERPLATE_LINE_MAX_WORDS` | `12` | words; the boilerplate substring check applies only at or under this length. A complete JD collapsed into one long line can legitimately END in "…All rights reserved."; only a genuinely short standalone nav/footer line should condemn the text. |
| `SCREEN_EVAL_CAP` | `80` (env `SCREEN_EVAL_CAP`) | count; hard ceiling on how many screened jobs reach the expensive per-job fan-out, so a wide pull can't blow past the frontend poll timeout. The screen RANKS by relevance and the top N are kept; the rest are recorded `screened_out` with reason `over_cap`. Applied even when the screen errors. |
| `SCREEN_BATCH_SIZE` | `150` (env `SCREEN_BATCH_SIZE`) | count; max jobs per screener LLM call. The screener enumerates every relevant/agency id, so its output cost scales with pool size — a 1000+ job pull overflows one call's `max_tokens` and truncates. The pool is chunked and results merged. |
| `SEARCH_MAX_DAYS_OLD_DEFAULT` | `7` | days; default listing-age cap for a run (0 = any age). User-selectable per run. |
| `SEARCH_PLAN_MAX_QUERIES` | `8` (env `SEARCH_PLAN_MAX_QUERIES`) | count; cap on planner output queries — bumped from 6 for more distinct role-variant coverage |
| `ADZUNA_MAX_PAGES` | `5` (env `ADZUNA_MAX_PAGES`) | count; max pages per query (path param) — bumped from 3: a nationwide query's fixed `results_per_page`x`pages` budget spreads over a much larger area than a geo-scoped one, so a concentrated niche field can starve otherwise |
| `ADZUNA_DEFAULT_RESULTS_PER_PAGE` | `20` | count; schema default for `results_per_page` |
| `ADZUNA_MAX_RESULTS_PER_PAGE` | `50` | count; Adzuna hard max (schema upper bound) |
| `HTTP_REFERER` | `"https://www.adzuna.com/"` | string; Referer on every page fetch + URL resolution. Deliberately provider-specific in a provider-agnostic layer: Adzuna's `/land/...` redirects 403 a referrer-less request even with a browser UA, and JD extraction starts by following exactly those redirects. Empirical, not principled — do not generalize it to the target host. |
| `HTTP_USER_AGENT` | `"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"` | string; UA for httpx + Playwright fetches. **Spec-corrected (was a `compatible; jdparser/1.0` polite-bot string):** Adzuna landing pages and many ATS bot-protections return **403** to a non-browser UA, which makes the JD-extraction flow (§7.5) impossible; a browser UA returns the full JobPosting JSON-LD. One UA constant for both fetch paths. |
| `ADZUNA_COUNTRY` | `"us"` | Adzuna country code (MVP-fixed, §8/§9) |
| `ADZUNA_BASE_URL` | `"https://api.adzuna.com/v1/api"` | URL base |
| `EVAL_FANOUT_CONCURRENCY` | `8` | count; max concurrent job-eval workers |
| `FETCH_TIMEOUT_S` | `20` | seconds; httpx request timeout |
| `PLAYWRIGHT_TIMEOUT_MS` | `30000` | milliseconds; Playwright nav/render timeout |
| `HTTP_MAX_RETRIES` | `2` | count; httpx retry attempts on 5xx/timeout |
| `PARSER_VERSION` | `"1.1.0"` | semver; resume parsing logic version (1.1.0: total_years_experience = whole-career span, not a field-specific figure). Part of the cache key — a bump invalidates cached profiles so they re-parse. |
| `SCHEMA_VERSION` | `"1.2.0"` | semver; ResumeProfile schema version (1.1.0 added `education`; 1.2.0 added `work_periods` for deterministic years). A bump invalidates cached profiles so they re-parse. |

### 6.2 Frontend constants — `web/lib/api.ts`

| Name | Value | Unit |
|---|---|---|
| `POLL_INTERVAL_MS` | `2000` | milliseconds; run-status poll cadence |
| `POLL_TIMEOUT_MS` | `300000` | milliseconds (5 min); give-up ceiling for a run |
| `NEXT_PUBLIC_API_BASE` | `"http://localhost:8000"` | env var; API origin |

### 6.3 LLM constants & model routing — `config.py`, `llm/client.py`

Provider is **OpenRouter** (OpenAI-compatible API) accessed via the **OpenAI Python
SDK** + **`instructor`** for Pydantic-validated structured output. Low-cost
open-weight models route by work type: **logic → gemini-3.1-flash-lite**, **text extraction →
Gemini 3.1 Flash Lite**.

| Name | Value |
|---|---|
| `OPENROUTER_BASE_URL` | `"https://openrouter.ai/api/v1"` |
| `MODEL_LOGIC` | `"google/gemini-3.1-flash-lite"` (logic) |
| `MODEL_GEMINI_FLASH_LITE` | `"google/gemini-3.1-flash-lite"` (text extraction) |
| `LLM_MAX_RETRIES` | `2` (instructor re-ask count on validation failure) |

**Per-node LLM config — `config.py: LLM_NODES` (parallel-enum mapping; do not infer):**

Generous defaults; **every field is overridable at startup via an env var** (below).

| node key | function | model | temperature | max_tokens | reasoning | Rationale |
|---|---|---|---|---|---|---|
| `profiler` | `profile_resume` | `MODEL_LOGIC` | `0.2` | `8000` | `off` | logic: seniority/domain/authorization inference + verbose `evidence[]` |
| `planner` | `plan_queries` | `MODEL_LOGIC` | `0.3` | `4000` | `off` | logic: synonym expansion (small output) |
| `jd_parser` | `parse_jd_requirements` | `MODEL_GEMINI_FLASH_LITE` | `0.1` | `6000` | `off` | text extraction: stated requirements from JD |
| `judge` | `judge_fit` | `MODEL_LOGIC` | `0.2` | `10000` | `medium` | logic: the one node that genuinely reasons; `met_requirements[]` + `rationale` + reasoning headroom (bumped from `low`: thematic/functional fit needs more than mechanical skill-list matching) |

**Sizing rationale (how the ceilings are qualified).** `max_tokens` is a **ceiling
billed only as generated**, not a reservation — a higher ceiling costs nothing unless
used, so it is free insurance against truncation. On OpenRouter, **reasoning tokens
draw from `max_tokens`**, so the real budget is `reasoning + JSON`. Ceilings = worst-case
serialized JSON + reasoning headroom + margin. Worst-case JSON output by schema:
`ResumeProfile` ~2.5K tokens (driven by `evidence[]`), `FitJudgment` ~1.7K
(`met_requirements[]` + `rationale`), `JobRequirements` ~1K, `SearchPlan` ~0.5K. The
`judge` ceiling is largest because it carries reasoning on top of the JSON; the two
pure-extraction logic nodes run reasoning `off` (schema-fill needs no chain-of-thought),
and Gemini Flash Lite barely reasons.

**Env overrides (read once at startup in `config.py`).** For each node, the four
fields fall back to the defaults above when the env var is unset. `<NODE>` ∈
`{PROFILER, PLANNER, JD_PARSER, JUDGE}`:

| field | env var | example |
|---|---|---|
| model slug | `LLM_MODEL_<NODE>` | `LLM_MODEL_JUDGE=google/gemini-3.1-flash-lite` |
| temperature | `LLM_TEMP_<NODE>` | `LLM_TEMP_PROFILER=0.1` |
| max_tokens (ceiling) | `LLM_MAX_TOKENS_<NODE>` | `LLM_MAX_TOKENS_PROFILER=12000` |
| reasoning | `LLM_REASONING_<NODE>` | `LLM_REASONING_JUDGE=high` |

`reasoning` ∈ `{off, low, medium, high}` → mapped to OpenRouter's control:
`off` → `extra_body={"reasoning": {"enabled": false}}`; otherwise
`{"reasoning": {"effort": <value>}}`. Canonical `config.py`:

```python
import os

def _node_cfg(node, model, temp, max_tokens, reasoning):
    return {
        "model":       os.getenv(f"LLM_MODEL_{node}", model),
        "temperature": float(os.getenv(f"LLM_TEMP_{node}", temp)),
        "max_tokens":  int(os.getenv(f"LLM_MAX_TOKENS_{node}", max_tokens)),
        "reasoning":   os.getenv(f"LLM_REASONING_{node}", reasoning),  # off|low|medium|high
    }

LLM_NODES = {
    "profiler":  _node_cfg("PROFILER",  MODEL_LOGIC,               "0.2", "8000",  "off"),
    "planner":   _node_cfg("PLANNER",   MODEL_LOGIC,               "0.3", "4000",  "off"),
    "jd_parser": _node_cfg("JD_PARSER", MODEL_GEMINI_FLASH_LITE, "0.1", "6000",  "off"),
    "judge":     _node_cfg("JUDGE",     MODEL_LOGIC,               "0.2", "10000", "medium"),
}
```

All calls: structured output via `instructor` (`response_model=<Schema>`,
`max_retries=LLM_MAX_RETRIES`) over the OpenAI SDK pointed at `OPENROUTER_BASE_URL`,
returning a validated Pydantic instance. No assistant-prefill / no Anthropic-only
`thinking`/`effort` params. **Truncation guard:** if a response hits the ceiling
(`finish_reason == "length"`), surface `LLM_TRUNCATED` (§6.4) with the offending node
so it's diagnosable and the operator can raise `LLM_MAX_TOKENS_<NODE>` — a truncation
must not masquerade as a validation failure. See IMPLEMENTATION_LLM.

### 6.4 Error codes (machine `code` values for `ErrorRecord`)

| Code | Raised by stage | Meaning |
|---|---|---|
| `RESUME_UNSUPPORTED_TYPE` | resume_extract | file is not pdf/docx/txt |
| `RESUME_EMPTY_TEXT` | resume_extract | extracted text below `MIN_RESUME_CHARS` |
| `LLM_API_ERROR` | profile/search_plan/parse/judge | OpenRouter transport/provider/rate-limit error from any LLM node |
| `LLM_TRUNCATED` | profile/search_plan/parse/judge | response hit `max_tokens` (`finish_reason="length"`) before completing — raise `LLM_MAX_TOKENS_<NODE>` (§6.3) |
| `PROFILE_INVALID` | profile | output failed `ResumeProfile` validation (after `LLM_MAX_RETRIES`) |
| `PLAN_INVALID` | search_plan | output failed `SearchPlan` validation (after `LLM_MAX_RETRIES`) |
| `PLAN_EMPTY` | search_plan | planner returned 0 queries |
| `ADZUNA_HTTP` | adzuna_search | non-2xx from Adzuna |
| `ADZUNA_AUTH` | adzuna_search | 401/403 (bad app_id/app_key) |
| `RESOLVE_FAILED` | resolve | redirect chain errored / no final URL |
| `FETCH_FAILED` | fetch | both http and Playwright failed |
| `JD_NOT_FOUND` | extract | no JSON-LD/ATS/readable text recovered |
| `JD_TOO_SHORT` | quality | `char_len < MIN_JD_CHARS` |
| `JD_NOISY` | quality | boilerplate ratio > `JD_BOILERPLATE_MAX_RATIO` |
| `PARSE_INVALID` | parse | output failed `JobRequirements` validation (after `LLM_MAX_RETRIES`) |
| `JUDGE_INVALID` | judge | output failed `FitJudgment` validation (after `LLM_MAX_RETRIES`) |
| `EVAL_COUNT_MISMATCH` | aggregate | reducer count ≠ deduped count (join bug) |

`MIN_RESUME_CHARS` and `MAX_RESUME_CHARS` are defined with units in §6.1.

---

## 7. Acceptance scenarios → exit-check mapping

Each maps to a phase exit-check in `BUILD.md`. Numbered §7.1–§7.6 so other docs can
cite them precisely.

- **§7.1 — Full JD gate.** A job appears in `qualified_jobs` **only if** it has a
  resolved `final_url`, `jd_char_len ≥ MIN_JD_CHARS` with quality `passed`, and a
  non-null `requirements` (valid `JobRequirements`). (Tier 2 + Tier 3.)
- **§7.2 — Fit gate.** A qualified job has `judgment.failed_dealbreakers == []`,
  `judgment.missing_hard_requirements == []`, `judgment.thematic_fit == true`, and a
  `met_requirements` entry (with evidence) for each required skill counted as met. (Tier 2.)
- **§7.3 — Confidence gate.** `judgment.decision == "qualified"` AND
  `judgment.confidence ≥ CONFIDENCE_THRESHOLD (0.75)`. Uncertain / below-threshold
  jobs are excluded from `qualified_jobs` (they land in `rejected`). (Tier 2.)
- **§7.4 — Cache hit.** Re-running the same resume yields a run with
  `resume_cache_hit == true` and **no** profiler LLM call. (Tier 2/3.)
- **§7.5 — Browser end-to-end.** A human uploads a resume in the web UI and sees ≥1
  qualified job card with company, title, `final_url`, and cited evidence within
  the run; failures appear only in the audit panel. (Tier 3.)
- **§7.6 — Determinism of display.** The visible set is computed by `is_qualified()`
  code (§ below), not by asking an LLM which jobs to show. (Code review + Tier 2.)

### `is_qualified()` (deterministic; implemented in `graph/nodes.py`)

**Normative:** a job is displayed if and only if ALL of the following hold. This spec
states the contract; `graph/nodes.py:is_qualified` is the single implementation. The
source is deliberately NOT reproduced here — it was, and the copy drifted from the
original (REFACTOR_AUDIT F15). A display gate with two definitions has none.

| # | Condition | Why |
|---|---|---|
| 1 | `status == "qualified"` | the §3.9 derivation agreed |
| 2 | a `judgment` is present | nothing to justify the match otherwise |
| 3 | `judgment.decision == "qualified"` | re-checked independently of `status` |
| 4 | `judgment.confidence >= CONFIDENCE_THRESHOLD` | §7.3 |
| 5 | `judgment.thematic_fit` | §7.2 — right profession, not just matching keywords |
| 6 | `judgment.failed_dealbreakers` is empty | §7.2 |
| 7 | `judgment.missing_hard_requirements` is empty | §7.2 |
| 8 | `requirements` is not null | §7.1 — the full JD was parsed |
| 9 | `final_url` is set | §7.1 — the posting resolved to a real page |
| 10 | `jd_char_len >= MIN_JD_CHARS` | §7.1 — enough JD text to judge on |

Conditions 3–7 duplicate checks that `status` already reflects. That redundancy is the
point: re-deriving them here means a stale or incorrect `status` can never leak a job
into the feed.

`status` is set upstream in the subgraph terminal nodes per the §3.9 derivation table
(NOT in `aggregate_matches`, which only filters). `is_qualified` is the final display
filter and a redundant guard: it re-checks every §7.1–§7.3 condition independently of
`status`, so a stale/incorrect `status` can never leak a job into the feed.

### Tier 3.5 applicability

This system has **no sustained-time trajectory** (no cooldowns, EMAs, rate-limit
ramps that flip user-visible state over a 60–90s window). The only async dynamic is
the bounded fan-out, whose invariant is a **count** at the join, not a trajectory
(§3.10). Therefore Tier 3.5 sustained-observation is **not required**. The fan-out
correctness is covered by the §3.10 timing invariant (`EVAL_COUNT_MISMATCH`) as a
Tier-2 assertion. (Documented per method: state explicitly when 3.5 is/ isn't used.)

---

## 7.7 Post-spec features (normative)

**Per-run LLM spend (`RunRecord.usage`).** Every OpenRouter call in a run is counted into
`LlmUsage` (`calls`, `prompt_tokens`, `completion_tokens`, `cost_usd`, `cost_complete`)
and written to the run record, on success AND on failure — a run that died at the judge
still cost money.

`cost_usd` is **OpenRouter's own figure**, requested per call via `usage.include`, not a
local price table: it stays correct when prices change or routing moves to a different
upstream provider. If any call returns no cost, `cost_complete` goes false and the UI
reports the total as a floor ("Cost at least …") rather than as authoritative.

Counting happens on instructor's `completion:response` hook, so re-asks after a validation
failure are included — they are billed, and omitting them would understate exactly the
runs that cost the most. Attribution is a `ContextVar` set per run, which reaches
LangGraph's fan-out workers; concurrent runs do not pool their spend. `usage` is `None` on
records written before this existed, which is distinct from a real zero.


Three user-visible features shipped after this spec was first written and are documented
here rather than being retrofitted into §3–§4 (REFACTOR_AUDIT F5).

**Relevance pre-screen + `screened_out`.** Before the expensive per-job fan-out, a cheap
batched LLM screen reads only the search result's title/company/snippet and drops jobs
outside the candidate's field. Survivors are ranked most-relevant-first and capped at
`SCREEN_EVAL_CAP`. Everything dropped is recorded in `RunRecord.screened_out` with a
`reason` of `off_field`, `agency`, or `over_cap`, and surfaced in the audit panel — the
user always sees what was skipped and why. Fault-tolerant: a screen failure, or a screen
that would drop everything, falls back to the whole deduped pool. The cap still applies.

**Recruitment-agency filter (`include_agencies`).** Agency status is a SECOND relevance
dimension, independent of field. The screener flags third-party recruiter/staffing
postings; they are screened out (`reason: "agency"`) unless the run opts in. When
included, they sort BEHIND direct employers so the evaluation budget fills with direct
employers first, and carry `EvaluatedJob.is_recruitment_agency` through to a UI badge.
The ordering is server-side only — the frontend renders the order it is given.

**Candidate feedback loop (`candidate_notes`).** `POST /api/feedback` accepts free-text on
any JUDGED job, in **both** directions:

| Job bucket | Correction | Typical note |
|---|---|---|
| `qualified_jobs` | false positive — "you told me I qualify, but I don't" | a constraint the resume didn't make obvious (`dealbreaker` / `preference`) |
| `rejected` | false negative — "you passed me over, but I do fit" | evidence the resume understated (`context`) |

`failures` are NOT eligible: a job that broke at fetch/parse has no judgment to disagree
with. The endpoint infers the direction from which bucket holds the job and passes it to
the distiller as `outcome`, because the two read identically as free text — without it a
"you were too harsh" correction would be distilled into a dealbreaker and make future
matching strictly worse.

An LLM distills the text into the candidate's persistent note list keyed by resume
`cache_key` — merging, not appending, so a new note supersedes one it contradicts
(including across directions: the candidate's latest word wins). Notes are typed
`dealbreaker` / `preference` / `context`. A triggered dealbreaker fails a job exactly like
a JD-stated one; a `context` note is authoritative fact about the candidate and **counts as
evidence**, so a skill asserted there can satisfy a required skill the resume omitted (§4.4,
fit_judge). That is what makes a false-negative correction actually change the next
verdict rather than just being recorded.

`DELETE /api/profiles/{cache_key}/notes/{index}` removes one note by its index in the
STORED list. It does NOT re-distill: deletion is the user overruling the model, and
running the survivors back through the LLM could reword them or argue the note back in.
The response returns the remaining list plus the deleted text.

`POST /api/profiles/{cache_key}/notes` adds feedback with NO job attached ("I won't
relocate", "the 2019 gap was contract work"), for the large class of corrections that
have no verdict to hang on. Same distillation, `job_context` null. `GET /api/profiles`
carries `notes` on each summary so the picker can show the list without a second fetch.

The note list IS the summary: distillation merges each new piece of feedback into it
rather than appending, so it can shrink when a new note supersedes an old one. The
frontend shows it under a selected saved resume, grouped dealbreaker-first.

Applies to the candidate's NEXT run only; evaluated jobs are never re-judged. `notes`
defaults to `[]`, so it needed no `SCHEMA_VERSION` bump.

## 8. Out of scope

- Authentication / accounts (single fixed `user_id = "local"`).
- A second job source. The `JobSource` seam exists (`jobsource/base.py`) with Adzuna
  as the only implementation — the abstraction is in place; multi-source is not.
- Countries other than `ADZUNA_COUNTRY`.
- Applying to jobs / write-back.
- A real database; durable cache and runs are JSON flat-files.
- Push/streaming run progress (WebSocket/SSE) — frontend polls.
- LLM-chosen display set — gating is deterministic code (§7).

## 9. MVP-stubbed surfaces (normative)

Consumers (UI explainers, README, e2e checks) MUST disclose these when describing
user-visible behavior.

| Surface | Stub behavior (MVP) | Eventual final form | File |
|---|---|---|---|
| User identity | fixed `user_id = "local"` | real auth / accounts | `config.py` |
| Run progress | pull-only polling of run record | SSE/WebSocket push | `server.py`, `web/lib/api.ts` |
| Resume cache store | JSON flat-files | Postgres / managed store | `cache/store.py` |
| Run records store | JSON flat-files | DB-backed | `runs/store.py` |
| LangGraph checkpointer | in-memory (`MemorySaver`) | persistent checkpointer | `graph/build.py` |
| Adzuna country | fixed `"us"` | per-query / user-selected | `config.py` |
| Resume cache eviction | none (manual delete) | TTL / LRU policy | `cache/store.py` |

## 10. Locked decisions (resolved during discovery)

1. **Casing.** Stored JSON, on-disk records, and API request/response bodies are
   **snake_case everywhere** (Python-native; FastAPI/Pydantic default). The web
   `lib/types.ts` mirrors snake_case fields directly — no camelCase remap layer.
   This supersedes the camelCase TS shape in `docs/system-overview.html`.
2. **Provider + model routing** per §6.3: **OpenRouter** via the OpenAI SDK +
   `instructor`; **Gemini 3.1 Flash Lite** (`google/gemini-3.1-flash-lite`) for logic
   nodes AND for literal JD extraction. The two routing constants (`MODEL_LOGIC`,
   `MODEL_GEMINI_FLASH_LITE`) currently resolve to the same slug — see §6.3.
3. **JD extraction** order: JSON-LD `JobPosting.description` → ATS-specific parser →
   `trafilatura` readable text. Playwright is a fallback only when static fetch
   yields thin/JS-gated content (§ EXTRACT).
4. **Cache key** = `sha256(text_hash : PARSER_VERSION : SCHEMA_VERSION)`; model not
   in the key.
5. **Fan-out** via LangGraph `Send`, reducers `operator.add`, join before aggregate.
6. **Push/pull reconciliation:** there is exactly one feed (pull). The run record is
   authoritative; the frontend polls to a terminal status. No push channel exists in
   MVP, so there is nothing to reconcile.
7. **Structured outputs via `instructor` + Pydantic.** All four LLM nodes call
   `client.chat.completions.create(response_model=<Schema>, max_retries=LLM_MAX_RETRIES)`
   through `instructor` (mode JSON), which validates against the Pydantic schema and
   re-asks the model on a validation failure before raising. This is the robustness
   layer for heterogeneous low-cost open models. (Native OpenRouter
   `response_format=json_schema` is the documented alternative if a model rejects
   JSON mode — see IMPLEMENTATION_LLM.)

## 11. Pin validation

All Python and JS pins in `docs/IMPLEMENTATION.md` §Foundations were probed against
their registries and found reachable (`pip index versions` / `npm view` are used
here as **read-only probe tools only**; the project installs via **uv** and **bun** —
PyPI 2026-06-25;
`openai`/`instructor` 2026-06-26). The **web pins were re-probed 2026-06-26** (`npm
view`) and are the current latest-stable releases, now pinned exact (incl.
`@types/{react,react-dom,node}`); Next 16.2.9 / React 19.2.7 are the newest stable
majors (Next 16 peers `react ^18.2||^19`). The OpenRouter model slug
(`google/gemini-3.1-flash-lite`) was confirmed present and priced
on the OpenRouter models API (`GET /api/v1/models`) **as of 2026-06-26**. Remaining
Python versions flagged "verify before relying" in the table were not individually
probed.
