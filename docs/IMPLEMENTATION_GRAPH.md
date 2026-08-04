# IMPLEMENTATION_GRAPH — LangGraph orchestration

> Owns state, top-level nodes, the fan-out, the evaluation subgraph, graph assembly,
> and the CLI entry. References: SPEC §3.1–§3.2, §3.9–§3.10, §4.1–§4.3, §6, §7.

## Purpose

Wire deterministic code and the four LLM nodes into a LangGraph state machine. Owns
the map-reduce fan-out and the per-job subgraph, and enforces the deterministic
qualification gate (`is_qualified`, SPEC §7).

## Files this area owns

- `packages/api/jdparser/graph/state.py`
- `packages/api/jdparser/graph/nodes.py`
- `packages/api/jdparser/graph/subgraph.py`
- `packages/api/jdparser/graph/build.py`
- `packages/api/jdparser/__main__.py`   (CLI: `uv run python -m jdparser <resume>`)
- tests: `tests/test_graph_smoke.py`

Imports from `llm/`, `adzuna/`, `extract/`, `cache/`, `resume/`, `runs/`, `config`.
Must NOT redefine their signatures — import and call.

## `state.py`

Copy `JobMatchState` and `JobEvalState` **verbatim** from SPEC §3.1 / §3.2.

## `nodes.py` — top-level nodes (SPEC §4.1)

```python
def extract_resume_text(state):
    text = extract_text(state["resume_file_path"])     # resume/extract_text.py
    return {"resume_text": text}

def fingerprint_resume(state):
    fp = compute_fingerprint(state["resume_file_path"], state["resume_text"])
    return {"resume_fingerprint": fp.cache_key}        # only cache_key is stored in state

def load_or_parse_profile(state):
    key = state["resume_fingerprint"]
    existing = get_profile(key)                         # cache/store.py
    if existing is not None:
        return {"resume_profile": existing.profile.model_dump(),
                "resume_cache_hit": True}
    profile = profile_resume(state["resume_text"])      # llm/resume_profiler.py (Gemini 3.1 Flash Lite)
    # recompute the full fingerprint here on miss (state only carries cache_key)
    fp = compute_fingerprint(state["resume_file_path"], state["resume_text"])
    rec = StoredResumeProfile(
        id=str(uuid4()), user_id=state["user_id"],
        cache_key=key,
        profile=profile, parser_version=PARSER_VERSION, schema_version=SCHEMA_VERSION,
        model=MODEL_LOGIC, created_at=now_iso(), updated_at=now_iso())   # profiler slug (§6.3)
    put_profile(rec)                                    # atomic write
    return {"resume_profile": profile.model_dump(),
            "resume_cache_hit": False}

def plan_searches(state):
    profile = ResumeProfile.model_validate(state["resume_profile"])
    plan = plan_queries(profile)                 # llm/search_planner.py (Gemini 3.1 Flash Lite)
    if not plan:
        raise JDParserError(code="PLAN_EMPTY", message="planner produced no queries")
    return {"search_plan": [q.model_dump() for q in plan]}

def search_jobs(state):
    queries = [AdzunaQuery.model_validate(q) for q in state["search_plan"]]
    results, errors = [], []
    for q in queries:                                   # continue-on-partial-failure (policy lives here)
        try:
            results.extend(run_search_plan([q]))        # jobsource/adzuna/client.py
        except JDParserError as e:
            errors.append(ErrorRecord(job_id=None, stage="job_search",
                                      code=e.code, message=e.message).model_dump())
    return {"job_results": results, "errors": errors}

def dedupe_jobs(state):
    return {"deduped_jobs": dedupe(state["job_results"])}    # jobsource/dedupe.py

def evaluate_jobs(state) -> list[Send]:                 # conditional edge fn, fan-out
    # inject the profile into each worker payload (SPEC §3.2, §4.1)
    return [Send("job_eval", {"job": j, "profile": state["resume_profile"], "result": []})
            for j in state["deduped_jobs"]]

def aggregate_matches(state):
    evaluated = state["evaluated_jobs"]
    if len(evaluated) != len(state["deduped_jobs"]):
        # join bug -> fatal: propagates out of invoke; API marks run failed (SPEC §3.10)
        raise JDParserError(code="EVAL_COUNT_MISMATCH",
                            message=f"{len(evaluated)} != {len(state['deduped_jobs'])}")
    qualified = [ej for ej in evaluated if is_qualified(ej)]    # SPEC §7
    return {"qualified_jobs": qualified}
```

`is_qualified()` — copy verbatim from SPEC §7. `now_iso()` / `JDParserError` /
constants live in `config.py`.

Failure policy at top level: `extract_resume_text`, `fingerprint_resume`,
`load_or_parse_profile`, `plan_searches` are **fatal** (raise → run fails). Their
exceptions are caught by the API runner (IMPLEMENTATION_API) which records the run
as `failed`. `search_jobs` is partial-tolerant (above). The fan-out is fully
fault-isolated (per-worker, below).

## `subgraph.py` — job-evaluation subgraph (SPEC §4.3)

Per-job state is `JobEvalState`. Each stage tries its work and, on a handled
failure, routes to `record_failure`. Use a conditional edge after each stage.

```python
def resolve_url(s):
    try:
        return {"final_url": resolve_final_url(s["job"].get("redirect_url", ""))}
    except JDParserError as e:
        return {"result": [_fail(s, "resolve", e)]}     # marker consumed by router

def fetch_page(s):
    try:
        fr = fetch(s["final_url"]); return {"fetched": fr.model_dump()}
    except JDParserError as e:
        return {"result": [_fail(s, "fetch", e)]}

def extract_jd(s):
    text = extract_jd_text(FetchResult.model_validate(s["fetched"]))
    if not text:
        return {"result": [_fail_code(s, "extract", "JD_NOT_FOUND")]}
    return {"jd_text": text}

def check_jd(s):
    q = check_quality(s["jd_text"])
    if not q.passed:
        return {"jd_quality": q.model_dump(),
                "result": [_fail_code(s, "quality", q.reasons[0])]}
    return {"jd_quality": q.model_dump()}

def parse_requirements(s):
    try:
        req = parse_jd_requirements(s["jd_text"])        # Gemini 3.1 Flash Lite
        return {"requirements": req.model_dump()}
    except JDParserError as e:
        return {"result": [_fail(s, "parse", e)]}

def judge_fit_node(s):
    try:
        profile = ResumeProfile.model_validate(s["profile"])    # injected via Send (SPEC §3.2)
        req = JobRequirements.model_validate(s["requirements"])
        j = judge_fit(profile, req)                      # Gemini 3.1 Flash Lite
        return {"judgment": j.model_dump()}
    except JDParserError as e:
        return {"result": [_fail(s, "judge", e)]}

def finalize(s):
    ej = _evaluated_job(s)                               # status per §3.9 table; strips _code/_msg
    return {"evaluated_jobs": [ej]}

def record_failure(s):
    marker = s["result"][0]                              # EvaluatedJob payload + transient _code/_msg
    err = ErrorRecord(job_id=marker["job_id"], stage=marker["failure_stage"],
                      code=marker.get("_code", "UNKNOWN"), message=marker.get("_msg", "")).model_dump()
    ej = {k: v for k, v in marker.items() if k not in ("_code", "_msg")}  # drop transients
    return {"evaluated_jobs": [ej], "errors": [err]}
```

Routing (conditional edges): after each non-terminal stage, a router checks whether
`s["result"]` was set (failure marker) → go to `record_failure`; else → next stage.
`finalize` and `record_failure` are the two terminal nodes; both emit a **clean**
`EvaluatedJob` into the parent `evaluated_jobs` reducer (SPEC §3.10). `result` itself
is never emitted to the parent.

Profile passing: the judge needs the `ResumeProfile`, but subgraph state is per-job —
so `evaluate_jobs` injects it into each `Send` payload under the `profile` key
(`JobEvalState.profile`, SPEC §3.2), and `judge_fit_node` reads `s["profile"]`. No
`_profile`-on-job hack.

`_evaluated_job(s)` builds an `EvaluatedJob` (SPEC §3.9) with `status` per the §3.9
derivation table (in particular: `decision=="qualified"` with `confidence <
CONFIDENCE_THRESHOLD` ⇒ `status="uncertain"`, NOT `not_qualified`). `jd_char_len` from
`jd_quality.char_len`; `final_url`, `source` (raw job dict), `requirements`, `judgment`
filled. The display gate is re-applied by `is_qualified` (SPEC §7).

`_fail`/`_fail_code` build a `failed` `EvaluatedJob` marker (`failure_stage` set) plus
transient `_code`/`_msg`; `record_failure` consumes and drops the transients.

## `build.py`

```python
def build_subgraph():
    g = StateGraph(JobEvalState)
    g.add_node("resolve_url", resolve_url)
    g.add_node("fetch_page", fetch_page)
    g.add_node("extract_jd", extract_jd)
    g.add_node("check_jd", check_jd)
    g.add_node("parse_requirements", parse_requirements)
    g.add_node("judge_fit", judge_fit_node)
    g.add_node("finalize", finalize)
    g.add_node("record_failure", record_failure)
    g.set_entry_point("resolve_url")
    # conditional edges: each stage -> next | record_failure  (router on s["result"])
    # ... check_jd -> parse_requirements | record_failure
    # judge_fit -> finalize ; finalize -> END ; record_failure -> END
    return g.compile()

def build_graph():
    sub = build_subgraph()
    g = StateGraph(JobMatchState)
    g.add_node("extract_resume_text", extract_resume_text)
    g.add_node("fingerprint_resume", fingerprint_resume)
    g.add_node("load_or_parse_profile", load_or_parse_profile)
    g.add_node("plan_searches", plan_searches)
    g.add_node("search_jobs", search_jobs)
    g.add_node("dedupe_jobs", dedupe_jobs)
    g.add_node("job_eval", sub)            # subgraph as a node (Send targets it)
    g.add_node("aggregate_matches", aggregate_matches)
    g.set_entry_point("extract_resume_text")
    g.add_edge("extract_resume_text", "fingerprint_resume")
    g.add_edge("fingerprint_resume", "load_or_parse_profile")
    g.add_edge("load_or_parse_profile", "plan_searches")
    g.add_edge("plan_searches", "search_jobs")
    g.add_edge("search_jobs", "dedupe_jobs")
    g.add_conditional_edges("dedupe_jobs", evaluate_jobs, ["job_eval"])  # fan-out
    g.add_edge("job_eval", "aggregate_matches")        # join (LangGraph waits for all Sends)
    g.add_edge("aggregate_matches", END)
    return g.compile(checkpointer=MemorySaver())       # MVP in-memory (SPEC §9)
```

Concurrency cap: pass `EVAL_FANOUT_CONCURRENCY` via the run config
(`graph.invoke(state, config={"max_concurrency": EVAL_FANOUT_CONCURRENCY, ...})`) —
LangGraph throttles concurrent `Send` branches to this cap. Document this in
`__main__.py` and the API runner.

## `__main__.py` (CLI)

```python
# uv run python -m jdparser <resume_path> [--user local] [--json]
# Builds initial JobMatchState, invokes the graph with a thread_id config,
# prints qualified_jobs (and a failures summary) as a table or JSON.
```
The CLI is a dev/verification convenience and the Tier-2 harness; the browser flow
(API + web) is the definition of done.

## Done when

- `tests/test_graph_smoke.py`: with `adzuna.client`, `extract.*`, and the LLM agents
  **monkeypatched/faked** (no network, no OpenRouter calls), assert the full acceptance set
  per BUILD.md Phase 5 — `len(evaluated_jobs)==len(deduped_jobs)`; qualified subset ==
  `is_qualified` (§7.6); null-`requirements`/null-`final_url` excluded (§7.1);
  non-empty `failed_dealbreakers`/`missing_hard_requirements` excluded (§7.2);
  `confidence=0.6` qualified→`uncertain`/excluded vs `0.8` included (§7.3); cache hit
  sets `resume_cache_hit True` **and** a counter confirms `profile_resume` was not
  called (§7.4); forced extract failure → `failed` + correct `failure_stage`; forced
  reducer-count mismatch → `aggregate_matches` raises `EVAL_COUNT_MISMATCH`.
- `uv run python -m jdparser tests/fixtures/sample_resume.pdf` runs end-to-end against live
  Adzuna + OpenRouter (orchestrator's M5 Tier-2 check) and prints ≥0 qualified jobs with
  no unhandled exception; a second run prints `cache_hit=true`.

## Why `nodes.py` is not split

At ~325 lines it holds the nine graph nodes plus the location-override algorithm plus
`is_qualified`. A split was evaluated during the refactor audit and **declined** for the
same reasons as `server.py`: cohesive around one pipeline, under the size threshold, and
cited by name across the spec. Revisit past ~500 lines. See
[`REFACTOR_AUDIT.md`](REFACTOR_AUDIT.md) F16.
