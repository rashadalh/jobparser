# IMPLEMENTATION_API — FastAPI service

> Owns the run lifecycle, background graph execution, and the polling endpoints.
> References: SPEC §5, §3.10 (run-record lifecycle), §6.2, §9.

## Purpose

Bridge the browser to the LangGraph pipeline. Accept a resume upload, run the graph
in the background, and expose run status/results for polling. The run record is the
single source of truth (pull-only; SPEC §10 item 6).

## Files this area owns

- `packages/api/jdparser/runs/store.py`
- `packages/api/jdparser/runs/__init__.py`
- `packages/api/jdparser/server.py`
- tests: `tests/test_api.py`

Imports `graph.build.build_graph`, `config`. `RunRecord` is defined in
`llm/schemas.py` (SPEC §5.2) — import it.

## `runs/store.py`

```python
def create_run(run_id: str, user_id: str, resume_file_path: str) -> RunRecord:
    rec = RunRecord(run_id=run_id, user_id=user_id, status="pending",
                    created_at=now_iso(), updated_at=now_iso(),
                    resume_cache_hit=None, qualified_jobs=[], failures=[],
                    rejected=[], errors=[], error=None)   # errors[] = ErrorRecord list (SPEC §5.2)
    _write(rec); return rec

def get_run(run_id: str) -> RunRecord | None:
    path = RUNS_DIR / f"{run_id}.json"
    return RunRecord.model_validate_json(path.read_text()) if path.exists() else None

def update_run(run_id: str, **fields) -> RunRecord:
    rec = get_run(run_id)              # must exist
    data = rec.model_dump() | fields | {"updated_at": now_iso()}
    rec = RunRecord.model_validate(data); _write(rec); return rec
```
`_write` is an atomic temp+`os.replace` to `RUNS_DIR / {run_id}.json` (same pattern
as `cache/store.py`). `RUNS_DIR = data/runs`.

## `server.py`

```python
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=[<web origin>], allow_methods=["*"], allow_headers=["*"])
_graph = build_graph()    # compiled once at import

@app.get("/api/health")
def health(): return {"status": "ok"}

@app.post("/api/runs", status_code=202)
async def start_run(background: BackgroundTasks, file: UploadFile = File(...)):
    run_id = str(uuid4())                      # generate FIRST so the upload can be named by it
    path = _save_upload(file, run_id)          # -> data/uploads/{run_id}.{ext}
    rec = create_run(run_id=run_id, user_id="local", resume_file_path=path)  # SPEC §8 fixed user
    background.add_task(_execute, run_id, path)
    return {"run_id": run_id, "status": "pending"}

@app.get("/api/runs/{run_id}")
def read_run(run_id: str):
    rec = get_run(run_id)
    if rec is None: raise HTTPException(404, "run not found")
    return rec
```

### `_execute(run_id, resume_path)` — background task

```python
def _execute(run_id: str, resume_path: str):
    update_run(run_id, status="running")
    init = {"run_id": run_id, "user_id": "local", "resume_file_path": resume_path,
            "resume_text": None, "resume_fingerprint": None, "resume_profile_id": None,
            "resume_profile": None, "resume_cache_hit": False, "search_plan": None,
            "adzuna_results": [], "deduped_jobs": [], "evaluated_jobs": [],
            "qualified_jobs": [], "errors": []}
    try:
        final = _graph.invoke(init, config={
            "configurable": {"thread_id": run_id},
            "max_concurrency": EVAL_FANOUT_CONCURRENCY})     # SPEC §6.1
        evaluated = final["evaluated_jobs"]
        update_run(run_id, status="completed",
                   resume_cache_hit=final["resume_cache_hit"],
                   qualified_jobs=final["qualified_jobs"],
                   failures=[e for e in evaluated if e["status"] == "failed"],
                   rejected=[e for e in evaluated if e["status"] in ("not_qualified", "uncertain")],
                   errors=final["errors"])     # ErrorRecord list -> RunRecord.errors (SPEC §3.10, §5.2)
    except JDParserError as e:
        update_run(run_id, status="failed", error=f"{e.code}: {e.message}")
    except Exception as e:                  # reason: never leave a run stuck in "running"
        update_run(run_id, status="failed", error=str(e))
```

Notes:
- `BackgroundTasks` runs after the response is sent, in the same process — adequate
  for MVP single-user. (A real queue is out of scope, SPEC §8.)
- The graph node functions are sync; `_graph.invoke` blocks the background task,
  which is fine (it's off the request path).
- `_save_upload(file, run_id)` saves the upload to `data/uploads/{run_id}.{ext}`
  (extension lowercased from the original filename) and returns the path. It does not
  reject unknown extensions itself — the graph's `extract_resume_text` raises
  `RESUME_UNSUPPORTED_TYPE` for non-{pdf,docx,txt}, and the run ends `failed` with
  that code surfaced in `error`.
- `EVAL_COUNT_MISMATCH` (a join bug) is raised by `aggregate_matches` as a
  `JDParserError`, caught here → run `failed` with that code in `error` (SPEC §3.10).

### CORS / env

- Allow the web origin (`http://localhost:3000` in dev) — read from
  `WEB_ORIGIN` env (default localhost:3000).
- Server listens on `:8000` (matches `NEXT_PUBLIC_API_BASE` default, SPEC §6.2).

## Push/pull (SPEC §10 item 6)

No SSE/WebSocket. The frontend polls `GET /api/runs/{run_id}` every
`POLL_INTERVAL_MS` until `status ∈ {completed, failed}`. The run record is
authoritative; partial results are not streamed. Disclose this MVP stub in the
audit-view copy (SPEC §9).

## Done when

- `tests/test_api.py` (TestClient, graph faked/monkeypatched):
  - `POST /api/runs` with a sample file → 202 `{run_id, status:"pending"}`; the
    background task drives the run to `completed`; `GET` then returns the populated
    `RunRecord` with `qualified_jobs`/`failures`/`rejected` partitioned correctly and
    `errors` carrying any non-per-job `ErrorRecord`s from the run (e.g. a faked
    Adzuna error appears in `errors`, not `error`); the upload was saved as
    `data/uploads/{run_id}.<ext>`.
  - `GET /api/runs/<unknown>` → 404.
  - a forced fatal error → run ends `failed` with `error` set, never stuck `running`.
  - `GET /api/health` → `{"status":"ok"}`.
- The orchestrator's Tier-3 check (BUILD.md final phase) drives this from a real
  browser, not just TestClient.
