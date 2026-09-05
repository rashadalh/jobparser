# IMPLEMENTATION_HANDLER — scheduled invoke

> Owns the Lambda entry, dual-mode image, day lock, graph invoke, archive
> wiring. References: SPEC §3.1, §3.3, §4.1, §4.4, §7.

## Purpose

One shot per America/Chicago day: lock, run the existing graph on the S3
resume, archive the `RunRecord`, call notify. Compose keeps serving HTTP.

## Files this area owns

- `packages/api/jdparser/schedule/handler.py`
- `packages/api/jdparser/schedule/bootstrap.py`
- `packages/api/Dockerfile` (dual-mode only; do not drop Playwright/Xvfb)
- `packages/api/entrypoint.sh`
- `packages/api/tests/test_schedule_handler.py`
- `packages/api/jdparser/config.py` — only if M0 left `JDPARSER_DATA_DIR` /
  schedule constants unfinished

Must NOT edit `IMPLEMENTATION_INFRA.md` Terraform, `notify.py` internals, or
`packages/web/`. May **import** store + notify.

## Dual-mode image

`entrypoint.sh` already starts Xvfb then `exec "$@"`. Change the end to:

```sh
# after Xvfb is up (keep the existing socket wait):
if [ -n "${AWS_LAMBDA_RUNTIME_API:-}" ]; then
  exec /app/.venv/bin/python -m jdparser.schedule.bootstrap
fi
exec "$@"
```

`bootstrap.py` (SPEC §4.1a): GetSecretValue `RUNTIME_SECRET_ARN`, export the
three API keys into `os.environ`, then `os.execv` venv python with
`["python", "-m", "awslambdaric", "jdparser.schedule.handler.handler"]`.

Dockerfile: after `uv sync --frozen --no-dev`:

```
RUN uv pip install --python /app/.venv/bin/python awslambdaric==4.0.2
```

Keep `CMD ["uv", "run", "uvicorn", ...]`. **Omit** Terraform `image_config`
entirely (an empty `command = []` would wipe CMD).

`JDPARSER_DATA_DIR` is set by Terraform to `/tmp/jdparser-data`. `config.py`
reads it at import (M0). Handler `mkdir`s `DATA_DIR / "profiles" | "runs" | "uploads"`.

## `handler.py`

```python
def schedule_date_from_event(event: dict[str, Any] | None) -> str:
    """SPEC §3.1. Validate YYYY-MM-DD."""

def partition_evaluated(
    evaluated: list[dict[str, Any]],
    qualified_from_graph: list[dict[str, Any]] | None = None,
) -> tuple[list, list, list]:
    """SPEC §4.4 copy of server.py _execute: qualified from graph list if given
    else is_qualified(); failures status==failed; rejected = complement."""

def build_run_record(*, run_id: str, final: dict[str, Any], error: str | None, usage: Any | None) -> RunRecord:
    """status completed if error is None else failed. Stamp now_iso.
    partition_evaluated(final['evaluated_jobs'], final.get('qualified_jobs')).
    Copy resume_cache_hit, errors, screened_out. Stamp usage (start_run_usage
    before invoke; as_dict on success and failure). user_id='local'.
    Do not call runs/store.py."""

def run_scheduled_search(
    *,
    event: dict[str, Any],
    store: ScheduleStore,
    send: Callable[[str], None],
    invoke_graph: Callable[..., dict[str, Any]],
    now_iso: str,
) -> ScheduleResult: ...

def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Production: s3_store_from_env, load_telegram_credentials, send_message
    closure, build_graph().invoke. Runtime API keys already in environ
    (bootstrap). Raises on fail. Returned dict always ok=True."""

def handler_with_deps(
    *,
    event: dict[str, Any],
    store: ScheduleStore,
    send: Callable[[str], None],
    invoke_graph: Callable[..., dict[str, Any]],
    now_iso: str,
) -> ScheduleResult:
    """Test entry. Never reads env. Same kwargs as run_scheduled_search."""
```

`ScheduleStore` is the protocol in `store.py` (or a `typing.Protocol` there).

### `run_scheduled_search` order (normative)

1. `schedule_date = schedule_date_from_event(event)`.
2. `lock = store.get_day_lock(schedule_date)`.
3. If `lock is None`: go to step 5 (create). Elif `lock.status == "completed"`
   → `ScheduleResult(ok=True, status="skipped", skip_reason="already_completed", ...)`.
4. Elif `lock.status == "running"` and age `< SCHEDULE_LOCK_STALE_S` → skipped
   `skip_reason="in_flight"`. Age uses `now_iso` parsed as aware UTC minus
   `started_at`. Age `>= 900` → takeover (step 5).
5. `run_id = str(uuid4())`. `put_day_lock(..., create_only=(lock is None))`.
   On `SCHEDULE_LOCK_HELD` → skipped `skip_reason="lock_held"`.
6. `bytes, suffix = store.get_resume(resume_key_from_event(event))`. Write to
   `Path(DATA_DIR) / "uploads" / f"{run_id}{suffix}"`.
7. `cfg = search_config_from_event(event)` or `store.get_search_config()`.
   `max_days_old` `None`/`<=0` → pass `None` into `initial_state`.
8. `store.hydrate_profiles(PROFILES_DIR)` (replaces dest files for listed keys).
9. `start_run_usage()`. `final = invoke_graph(...)` as SPEC §4.4.
10. Build `RunRecord` via `partition_evaluated`. On exception: failed
    `RunRecord` with `error=...` and usage stamped, still archive, then fail
    the lock (step 14).
11. `store.persist_profiles(PROFILES_DIR)` inside try/except: log and continue
    on `SCHEDULE_S3`.
12. `key = store.put_archived_run(ArchivedRun(...))`. Inner `run` must
    `RunRecord.model_validate`.
13. `ns, n = notify_new_jobs(...)` only on graph success. Graph failure: skip
    notify.
14. `put_day_lock` completed or failed.
15. Failure path: `raise` the original `JDParserError`.
16. Return `ScheduleResult(ok=True, status="completed", ...)`.

Skipped paths never call `invoke_graph` or `send`.

`invoke_graph` in tests is a callable `(run_id, path, cfg) -> final_state_dict`
with `evaluated_jobs`, `resume_cache_hit`, `errors`, `screened_out`.

## Tests (`test_schedule_handler.py`)

Use in-memory store + recording `send`. No AWS, no OpenRouter.

- First run: completed, archive inner `run` validates as `RunRecord`, send called, day lock completed.
- Second run same date: skipped, send not called, invoke_graph not called.
- In-flight lock younger than stale: skipped.
- Stale running lock (`started_at` 901 s before `now_iso`): takeover, invoke happens.
- Missing resume: day lock ends `failed`, invoke not called, send not called.
- Pre-seeded `NotifiedSet` with a `job_id` that the stub graph marks qualified:
  `send` texts do not contain that job's title (SPEC §7.3).
- Stub `evaluated_jobs` with `status` failed / uncertain / qualified-but-not-
  `is_qualified`: those titles absent from `send` (SPEC §7.4). Screened-out is
  not in `evaluated_jobs`.
- Files this area also owns `bootstrap.py`: unit-test the env mapping with a
  fake secrets client (do not exec RIC in pytest).

## Done when

`uv run pytest tests/test_schedule_handler.py tests/test_schedule_store.py
tests/test_schedule_notify.py` green (includes §7.2, §7.3, §7.4, §7.7).
`docker compose up` + `curl /api/health` → `"ok"`. Do not require a live
Lambda for this area's pytest exit-check.
