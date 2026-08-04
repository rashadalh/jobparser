---
name: jdparser-refactor-audit
description: Audit and refactor THIS codebase (jdparser — resume-driven job matcher; FastAPI + LangGraph + Next.js). Knows the repo's layout, verification commands, and its specific landmines: persisted enum literals, LangGraph reducer channels, contextvar propagation, provider JSON null-handling, prompt packaging. Use for structural debt, a feature that spans the stack, or investigating a bug reported from a live run. Derived from the generic refactor-audit method plus what the 2026-08-04 audit of this repo actually got wrong.
---

# jdparser Refactor Audit

The generic refactor-audit method, specialized to this repo. It carries what the last
full audit found, what it **missed**, and the landmines that cost real debugging time.

Read "Landmines" (§4) before touching code. Most of it is non-obvious, and every entry is
there because it actually bit.

---

## 1. The codebase in one screen

`packages/api` (Python 3.11, uv) and `packages/web` (Next.js 16, bun). One user, local,
no auth.

```
resume → text → fingerprint → profile (cached) → plan queries → search Adzuna
      → dedupe → relevance screen → FAN OUT per job:
            resolve URL → fetch → extract JD → quality gate → parse reqs → judge fit
      → aggregate → deterministic display gate (is_qualified)
```

| Path | What lives there |
|---|---|
| `jdparser/graph/` | The pipeline. `state.py` channels, `nodes.py` top-level, `subgraph.py` per-job, `build.py` wiring only |
| `jdparser/jobsource/` | Where postings come from. `base.py` protocol, `adzuna/` the one implementation, `dedupe.py` generic |
| `jdparser/llm/` | All six agents, `schemas.py` (owns **every** model), `client.py` (`_call`), `prompts/*.md`, `usage.py` (cost) |
| `jdparser/extract/` | URL → JD text: resolve, fetch, jsonld/ats/readable, quality |
| `jdparser/cache/`, `runs/` | JSON flat-file stores under `data/` |
| `jdparser/server.py` | FastAPI + the background run executor |

Each subpackage has a `README.md`. `docs/REFACTOR_AUDIT.md` is the last audit, pinned to
`938203f`; its `file:line` citations refer to that tree, not HEAD.

## 2. Verification (never guess these)

| What | Command | Green baseline |
|---|---|---|
| API tests | `cd packages/api && uv run pytest` | 181 passed, 1 warning |
| API types | `cd packages/api && uv run mypy --strict jdparser` | clean, 41 files |
| Web types | `cd packages/web && bun run typecheck` | clean |
| Web build | `cd packages/web && bun run build` | clean |
| Live app | `docker compose up -d --build` | web :3000, api :8000 |

The 1 pytest warning is a pre-existing LangGraph deprecation, not a regression.

**`packages/web` has no test suite.** Typecheck and build are the *entire* regression
signal for frontend work. Say so when reporting; don't imply coverage that doesn't exist.

Anything touching `graph/`, `llm/schemas.py`, or `jobs.py` runs all four.

## 3. Finding categories

The 12 generic ones still apply (god files, DRY, hand-rolled stdlib, hardcoded
assumptions, doc sprawl, embedded blobs, missing docs, logic in "helper" files, flat
folders, stubs, terminology drift, missing subfolder READMEs).

**These seven are additions, and each one exists because the last audit missed a real
bug.** Weight them higher.

### 3.1 Claims the code doesn't honor (highest value)

The comments here are unusually good, which makes them unusually easy to trust. The two
most expensive bugs were both a confident comment describing behavior that didn't exist.

> `HTTP_MAX_RETRIES: int = 2  # httpx retry attempts on 5xx/timeout`
> `HTTPTransport(retries=)` retries **connection** errors only. It had never retried a 5xx.
> Three lines disproved it. Six queries, six 503s, zero jobs.

For every load-bearing claim in a name or comment — retries, timeouts, atomicity,
thread-safety, caching, "single source of truth", "always", "never" — **write the
experiment that proves it.** Don't read it.

### 3.2 Failure blast radius

Two of the worst bugs were exception *scope*, not logic.

- `dedupe_jobs` had no `try/except` and is a top-level node, so one null field failed the
  whole run.
- `fetch()` wrapped a required render and an optional headed retry in one `try`, so the
  optional one could kill the required one.

Map each failure domain. For every optional or best-effort step, name what its failure can
take down. Fan-out workers are fault-isolated; top-level nodes are not.

### 3.3 The runtime surface

Read `Dockerfile`, `entrypoint.sh`, `docker-compose.yml`, and `pyproject.toml` packaging as
**behavior**, not documents. Ask: does deployment actually deliver what the code assumes?

That is the `DISPLAY` bug (an entrypoint `export` reaches only the process it `exec`s,
never `docker exec`) and the prompt-packaging risk (`.md` files ship only because
`pyproject` lists them under `artifacts`).

### 3.4 Write-only data

Per datum: **can the user see it, correct it, and delete it?** `candidate_notes` were fed
to the judge but invisible to the person they described, and permanent. That took three
separate commits to close.

### 3.5 User-facing text

Copy is an artifact. Check for internal vocabulary reaching users ("off-field", "eval
budget"), raw enums rendering into prose (`not_qualified`), and dev-process framing in
docs ("definition of done"). House style to avoid: em dashes, clauses stapled where a
full stop belongs.

### 3.6 Cost and resource accounting

This system spends money per request. Anything that consumes a metered resource should
report what it consumed, per run, visibly.

### 3.7 Feature symmetry

For every action taken on the user's behalf, is there a corrective path in **both**
directions? Feedback accepted "you said I qualify but I don't" and had no route for "you
passed me over and shouldn't have."

## 4. Landmines

Every one of these has bitten. Violating any silently produces wrong behavior that no
existing test catches.

**Persistence**

- `ErrorStage` / any `Literal` in `llm/schemas.py` is **persisted** in `data/runs/*.json`.
  `runs/store.list_runs` swallows validation failures with `except: continue`, so removing
  a value deletes run history from the UI with no error, no log, no failing test. **Widen
  the union, never replace.** `"adzuna_search"` is kept for exactly this reason and looks
  like dead code.
- `EvaluatedJob.source` must stay byte-identical to the raw provider payload. Build it
  from `Job.raw`, never from normalized fields.
- Pydantic ignores unknown fields on load, so *dropping* a stored field needs no migration.
  *Adding* a required one invalidates every record.
- The resume cache key is content-addressed on extracted **text** plus
  `PARSER_VERSION`/`SCHEMA_VERSION`. Bumping either orphans every cached profile, which is
  intended.

**LangGraph**

- `JobEvalState` must re-declare `evaluated_jobs`/`errors` reducer channels or subgraph
  emissions are silently dropped and `aggregate_matches` raises `EVAL_COUNT_MISMATCH`.
- A `JobEvalState` field sharing a `JobMatchState` channel name auto-propagates back and
  collides across concurrent `Send` branches. That's why it's `job`/`notes`, not
  `resume_profile`/`candidate_notes`.
- Channels carry `.model_dump()` payloads, never live models.
- Node functions registered with `add_node` must name their first parameter `state`
  (mypy matches the Protocol by name). Routers are exempt.

**Concurrency**

- `contextvars` DO reach LangGraph's fan-out workers (langchain-core copies the context)
  but do **not** reach a bare `threading.Thread`. Per-run cost accounting depends on the
  first; anyone hand-rolling a thread gets silent under-counting. Both facts are pinned in
  `tests/test_usage.py`.

**Provider I/O**

- Adzuna sends keys **present-but-null**. `.get(k, {})` covers absent only. Use
  `(x or {})`. All normalization belongs in `jobsource/adzuna/client._to_job`, the one
  place that touches raw provider JSON.
- `HTTPTransport(retries=)` does not retry HTTP error responses. Retrying a 5xx takes an
  explicit request loop.
- Never paste a provider's HTML error body into a user-facing message.

**LLM**

- instructor re-asks on validation failure and **every attempt is billed**.
  `create_with_completion` returns only the final one; count on the
  `completion:response` hook.
- OpenRouter returns cost when sent `usage: {"include": true}`. Verified: the unit is USD,
  and the SDK exposes it as both `CompletionUsage.cost` and `model_extra["cost"]`.
- Prompts live in `llm/prompts/*.md`. `planner.md` is a **template** with `%(...)d`
  placeholders interpolated at import; a plain move breaks it.

**Frontend**

- Display order is not storage order. The notes panel sorts dealbreakers first, so any
  index sent to the API must be the **stored** index. Getting this wrong deletes a
  different row than the one clicked, silently.
- `SPEC §9` requires the README and UI to disclose the MVP-stubbed surfaces (single local
  user, polling not streaming, flat files, in-memory checkpointer, US-only, no cache
  eviction). Rewrite that prose, never delete it.

**mypy --strict**

- `int ** int` is typed `Any`. Use a float base.
- `type[X]` is invariant; a protocol member an implementation needs to narrow must be a
  read-only `@property`.

## 5. Process

Follow the generic method (lay of the land → mechanical inventory → hunt → amplify → draft
→ self-consistency → gate → fold → execute → handoff), with these corrections:

**Grep without extension filters.** The last audit's amplification grep used
`--include="*.md" --include="*.py" ...` and silently skipped `.env.example`, which stayed
wrong for the entire audit and every phase after it. `Dockerfile`, `entrypoint.sh`, and
`docker-compose.yml` are excluded by the same mistake, and two of the three later bugs
lived in those files. Use `git grep -l <pattern>` with no filter, then narrow.

**Run the app.** Before drafting findings: `docker compose up -d --build`, upload a
resume, open every panel. Three real problems were invisible to static reading — a panel
nobody could find, copy that read badly in situ, and cost being unknowable. Budget ten
minutes.

**Make one live call when a provider's behavior is load-bearing.** Cheap and decisive. A
single OpenRouter call (~$0.0002) settled the cost-field shape and its units; one respx
experiment settled the retry question. Ask before spending the user's money, then do it.

**Audit what the tests assert, not just their structure.** 137 tests passed cheerfully
over a crash-on-null bug, a retry that never retried, and an escalation that could kill a
fetch. Ask what the suite *fails to claim*.

**Prove a regression test fails first.** For any bug fix, run the new test against the
unfixed code and show the failure. A test that passes either way proves nothing.

## 6. Execution

- **Branch** `refactor/<topic>` cut from `main`. Idempotent checkout: exists → `checkout`,
  doesn't → `checkout -b`.
- **One commit per unit of work.** The log is the ledger; a fresh agent reconstructs state
  from `git log --oneline main..HEAD` alone.
- **Commit messages carry the reasoning**, especially: why a decision that looks wrong to
  a code-only reader is right, what was verified and how, and what was deliberately not
  done.
- **Tiers per change:** compiles → existing suite passes unmodified (except symbols that
  moved) → the domain constraint for *this* change, stated explicitly. Persisted-shape
  changes always get tier 3.
- **Escalate** design decisions not already settled. Don't improvise architecture mid-task.

## 7. Reporting

State plainly what was verified versus assumed. When a check was skipped or a signal
doesn't exist (frontend tests), say so rather than implying coverage. If a finding is
declined, record the reasoning where a future reader will hit it, so it reads as a
decision instead of an oversight.
