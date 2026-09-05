# SPEC — Daily job recommendations (system contract)

> Canonical contract for this plane. Implementation docs reference it by section.
> Tone: precise, terse. Code blocks here are **ground truth** — if the
> implementation diverges, update this doc.
>
> Graph, `RunRecord`, `EvaluatedJob`, and `is_qualified()` remain root
> `/SPEC.md`. This document owns schedule, S3, Lambda, and Telegram.

---

## 1. System overview

```
EventBridge Scheduler 07:00 America/Chicago (gated by enable_schedule)
Monday: each location, last week. Tuesday–Friday: each location, last 24h.
        │  input.scheduled_time = <aws.scheduler.scheduled-time>
        ▼
Lambda (ECR image = packages/api; not uvicorn)
        ├─ day lock   s3://{bucket}/state/day/{schedule_date}.json
        ├─ resume     s3://{bucket}/resume/current  →  /tmp/jdparser-data/resume
        ├─ config     s3://{bucket}/config/search.json
        ├─ hydrate    s3://{bucket}/profiles/*.json →  $JDPARSER_DATA_DIR/profiles
        ├─ graph.invoke  (root SPEC §1 pipeline; same is_qualified gate)
        ├─ persist    profiles + runs/{schedule_date}/{run_id}.json
        └─ notify     Telegram new qualified jobs; merge state/notified.json

CloudWatch alarm ──► SNS ──► zip Lambda telegram-relay ──► Telegram
                    (optional; enable_telegram_alerts)
```

The browser app is unchanged. Compose still runs FastAPI. The scheduled path
never starts `uvicorn` and never writes the local compose volume.

**Push vs pull.** This plane has one push channel (Telegram recs) and one pull
channel (S3 objects). **S3 is authoritative** for runs, profiles, resume bytes,
search config, day locks, and the notified set. Telegram is a best-effort
delivery of a derived subset (new `is_qualified` jobs). A lost Telegram message
is recovered on the next successful send of that `job_id` only if the notified
set was not updated; after a successful send, S3 notified-set wins and the job
is not re-sent. There is no webhook and no polling consumer in this plane.

---

## 2. Repo layout

Every file this build may create appears here. Root product files already on
disk are not relisted except where this plane edits them.

```
jdparser/
├── specs/
│   ├── README.md
│   └── auto-job-recommendations/
│       ├── PLAN.md
│       ├── SPEC.md
│       ├── BUILD.md
│       ├── IMPLEMENTATION.md
│       ├── IMPLEMENTATION_HANDLER.md
│       ├── IMPLEMENTATION_STORE.md
│       ├── IMPLEMENTATION_NOTIFY.md
│       └── IMPLEMENTATION_INFRA.md
├── infra/                                   # Terraform root (NOT a module)
│   ├── versions.tf
│   ├── providers.tf
│   ├── variables.tf
│   ├── outputs.tf
│   ├── ecr.tf
│   ├── s3.tf
│   ├── iam.tf
│   ├── lambda.tf
│   ├── scheduler.tf
│   ├── secrets.tf
│   ├── sns.tf
│   ├── alarms.tf
│   ├── telegram_relay.tf
│   ├── terraform.tfvars.example
│   ├── runtime.secret.example.json
│   ├── telegram-alerts.secret.example.json
│   ├── RUNBOOK.md
│   ├── scripts/push-api-image.sh
│   └── lambda/telegram_relay/handler.py     # stdlib only; zip root; handler.handler
├── packages/api/
│   ├── Dockerfile                           # EDIT: dual-mode entry
│   ├── entrypoint.sh                        # EDIT: Xvfb + RIC vs uvicorn
│   ├── pyproject.toml                       # EDIT: boto3 pin
│   ├── uv.lock                              # EDIT: lock boto3
│   ├── jdparser/
│   │   ├── config.py                        # EDIT: JDPARSER_DATA_DIR; schedule constants
│   │   └── schedule/
│   │       ├── __init__.py
│   │       ├── handler.py                   # Lambda handler (config import OK after bootstrap exec)
│   │       ├── bootstrap.py                 # fetch runtime secret → environ; exec RIC
│   │       ├── store.py                     # S3 I/O
│   │       ├── notify.py                    # Telegram recs
│   │       └── schemas.py                   # ScheduleSearchConfig, DayLock, …
│   └── tests/
│       ├── test_schedule_handler.py
│       ├── test_schedule_store.py
│       └── test_schedule_notify.py
```

Root files this plane **may** edit (pointers + DATA_DIR + pins only):
`PLAN.md`, `SPEC.md`, `docs/IMPLEMENTATION.md`, `README.md`, `.gitignore`,
`.env.example`.

Root files this plane **must not** edit: `graph/`, `llm/` (schedule models live
in `schedule/schemas.py`; do not touch `llm/schemas.py`), `extract/`,
`jobsource/`, `server.py`, `packages/web/`. Do not extract helpers from
`server.py`. Copy the partition rules below.

---

## 3. Data types

JSON on the wire and in S3 is **snake_case**. Pydantic v2 models live in
`jdparser/schedule/schemas.py`. Root `RunRecord` / `EvaluatedJob` /
`StoredResumeProfile` stay in `jdparser/llm/schemas.py`.

### 3.1 `schedule_date`

`YYYY-MM-DD` calendar date in `America/Chicago`.

If `event["scheduled_time"]` is present (Scheduler context attribute
`<aws.scheduler.scheduled-time>`, UTC ISO-8601 such as `2022-03-22T18:59:43Z`):
`datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(ZoneInfo("America/Chicago")).date().isoformat()`.
Do **not** slice `[:10]` off the UTC string.

Else: `datetime.now(ZoneInfo("America/Chicago")).date().isoformat()`.

### 3.2 `ScheduleSearchConfig`

```python
class ScheduleSearchConfig(BaseModel):
    locations: list[str] | None = None   # None = profile inferred locations
    broaden_search: bool = True
    max_days_old: int | None = 7         # days; None or <=0 = any age
    include_agencies: bool = False
```

Maps onto `initial_state(..., search_locations=locations, broaden_search=...,
max_days_old=..., include_agencies=...)`. Pass `max_days_old=None` into
`initial_state` when the config value is `None` or `<= 0` (any age). Default
`7` means 7 days. Missing S3 object → these defaults (not a hard fail).
Invalid JSON → `SCHEDULE_CONFIG_INVALID` (hard fail).

### 3.3 `DayLock`

```python
DayLockStatus = Literal["running", "completed", "failed"]

class DayLock(BaseModel):
    schedule_date: str          # 3.1
    status: DayLockStatus
    run_id: str
    started_at: str             # ISO-8601 UTC
    updated_at: str             # ISO-8601 UTC
    run_s3_key: str | None = None
    error: str | None = None
    new_notified: int | None = None  # count; set on completed
```

### 3.3-lifecycle `state/day/{schedule_date}.json`

| Event | Condition | Mutation |
|---|---|---|
| Enter `running` | object does not exist, **or** existing `status=="failed"` | `PutObject` of a new `DayLock(status="running")`. First-time create uses `If-None-Match: *` (HTTP 412 → `SCHEDULE_LOCK_HELD`). Takeover of `failed` overwrites (`create_only=False`). Versioning: a delete-marker or leftover version of this key still fails `If-None-Match`. |
| Skip | existing `status=="completed"` | no write; handler returns `skipped` / `already_completed` |
| Skip-in-flight | existing `status=="running"` AND age `<` `SCHEDULE_LOCK_STALE_S` | no write; return `skipped` / `in_flight`. Age = `now_utc - started_at` (`started_at` parsed as aware UTC). **Equal** to 900 s is **not** in-flight (takeover). |
| Takeover stale | existing `status=="running"` AND age `>=` `SCHEDULE_LOCK_STALE_S` | overwrite with new `running` + new `run_id`. A **timeout** kills the process with no `failed` write, so the lock stays `running` until this row. |
| Exit `completed` | graph **and** notify finished without raise | `status="completed"`, `run_s3_key` set, `new_notified` set |
| Exit `failed` | hard fail after lock acquired (including notify fail after a completed graph) | `status="failed"`, `error` set; handler then **raises**. Lambda **async retries are 0** (SPEC §6); Scheduler retries only **invoke** failures (IAM/throttle), not handler exceptions. Same-day retry is an operator `lambda invoke` or a later Scheduler delivery that takeovers `failed`. |

No TTL. Objects are not deleted by this plane.

### 3.4 `NotifiedSet`

```python
class NotifiedJob(BaseModel):
    job_id: str
    final_url: str | None
    title: str
    company: str
    first_notified_at: str      # ISO-8601 UTC
    schedule_date: str

class NotifiedSet(BaseModel):
    jobs: dict[str, NotifiedJob]  # keyed by job_id
    urls: dict[str, str]          # final_url → job_id; omits None urls
    updated_at: str
```

### 3.4-lifecycle `state/notified.json`

| Event | Condition | Mutation |
|---|---|---|
| Enter | object missing | treat as empty `{jobs:{}, urls:{}, updated_at}`; first successful notify `PutObject`s the file |
| Suppress | `job_id in jobs` OR (`final_url` is a non-empty string AND `final_url in urls`) | job is **not** sent |
| Add | Telegram send for that job returned success | insert `jobs[job_id]` and, if `final_url` nonempty, `urls[final_url]=job_id`; `PutObject` immediately (per successful chunk, not after the batch). Last write wins; reserved concurrency 1 is the mutex; no ETag. |
| URL collision | new `job_id`, `final_url` already in `urls` | `is_new` is false (suppressed) |
| ID collision | `job_id` already in `jobs` (even if URL changed) | `is_new` is false (suppressed) |
| Exit | never | no eviction in v1 |

`qualified_jobs` in the run JSON still lists every qualified job for that run,
including ones already in `NotifiedSet`. Telegram is the only filtered surface.

### 3.5 `ScheduleResult` (Lambda return)

```python
ScheduleStatus = Literal["completed", "skipped", "failed"]

class ScheduleResult(BaseModel):
    ok: bool
    status: ScheduleStatus
    schedule_date: str
    run_id: str | None = None
    run_s3_key: str | None = None
    new_notified: int = 0
    skip_reason: str | None = None
```

`failed` is never a successful Lambda return: the handler raises after writing
the day lock. The type exists so tests can inspect a caught path.
Returned dicts always have `ok=True`.

### 3.5-mapping status enums

| Situation | `DayLock.status` | `ScheduleResult.status` | inner `RunRecord.status` | Telegram recs |
|---|---|---|---|---|
| First successful tick | `completed` | `completed` | `completed` | header+jobs or heartbeat |
| Same-day after completed | unchanged | `skipped` / `already_completed` | (no new run) | none |
| In-flight lock | unchanged `running` | `skipped` / `in_flight` | (no new run) | none |
| Lock 412 | unchanged | `skipped` / `lock_held` | (no new run) | none |
| Graph fatal | `failed` | (raise) | `failed` (still archived if built) | none |
| Graph ok, notify raises | `failed` | (raise) | `completed` (archive already written) | prefix of chunks that succeeded; those job_ids are in `NotifiedSet` |
| Process timeout | stays `running` | n/a | maybe partial / none | none until stale takeover |
| CloudWatch alarm | n/a | n/a | n/a | relay `jdparser alarm:…` only |

### 3.6 Resume object

Key `resume/current` — raw file bytes. Suffix is sniffed from **magic bytes**,
not Content-Type (compose `aws s3 cp` often sends `binary/octet-stream`):
`%PDF` → `.pdf`; ZIP/`PK` with `word/` in the first 4 KiB → `.docx`; else `.txt`.
Missing object → `SCHEDULE_RESUME_MISSING` (hard fail).

### 3.7 Run archive object

Key `runs/{schedule_date}/{run_id}.json`. Body is root `RunRecord.model_dump()`
plus two extra keys the archive wrapper adds (not fields on `RunRecord`):

```python
class ArchivedRun(BaseModel):
    schedule_date: str
    search_config: ScheduleSearchConfig
    run: dict   # RunRecord.model_dump(); extra="forbid" is NOT applied to this dict
```

**Enter:** after the graph reaches a terminal status (`completed` or `failed`)
and a `RunRecord` has been built. **Exit:** never. Writes use `If-None-Match: *`
on the run key (immutable per `run_id`).

### 3.8 Profile objects

Key `profiles/{cache_key}.json`. Body is root `StoredResumeProfile`. Hydrated
into `$JDPARSER_DATA_DIR/profiles` before `graph.invoke`; uploaded after invoke
for every local profile file that exists (content-addressed replace is OK;
same key may be rewritten when notes change).

---

## 4. Interfaces

### 4.1 Lambda handler

```python
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """EventBridge Scheduler or `aws lambda invoke`. Returns ScheduleResult.model_dump()."""
```

`event` keys used:

| Key | Source | Required |
|---|---|---|
| `scheduled_time` | Scheduler input `<aws.scheduler.scheduled-time>` | no; default now Chicago |
| `search` | Manual invoke or Scheduler: `texas` / `new-york` / `chicago` / `boston` | no; omitted = `config/search.json` |
| `max_days_old` | Manual invoke or Scheduler; integer days (`1` or `7`) | no; omitted = preset default `7` |

`search` selects a location preset. Optional `max_days_old` overrides the
preset window (`7` last week, `1` last 24 hours). Each preset has its own day
lock at `state/day/{schedule_date}/{search}.json`, so four location ticks the
same Chicago day do not skip each other. Unknown `search` or non-integer
`max_days_old` → `SCHEDULE_CONFIG_INVALID`. Other unknown keys ignored.

EventBridge Scheduler (gated by `enable_schedule`):

- Monday 07:00 America/Chicago: each location, `max_days_old=7`
- Tuesday–Friday 07:00 America/Chicago: each location, `max_days_old=1`
- No weekend ticks

Reserved concurrency is 1, so the four locations stagger by 16 minutes
(7:00 / 7:16 / 7:32 / 7:48). The 07:00 Scheduler input always includes
`search` and `max_days_old`.

### 4.1a Runtime secrets (must precede `jdparser.config` import)

`config.py`, `llm/client.py`, and the Adzuna client bind `OPENROUTER_API_KEY` /
`ADZUNA_APP_ID` / `ADZUNA_APP_KEY` at **import**. Setting `os.environ` inside
`handler()` is too late if those modules are already loaded.

`schedule/bootstrap.py` is the Lambda entry **process**:

1. `boto3.client("secretsmanager").get_secret_value(SecretId=os.environ["RUNTIME_SECRET_ARN"])`
2. JSON keys → `os.environ` (`OPENROUTER_API_KEY`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`)
3. `os.execv` the venv python as `python -m awslambdaric jdparser.schedule.handler.handler`

`entrypoint.sh` Lambda branch: start Xvfb, then `exec /app/.venv/bin/python -m jdparser.schedule.bootstrap`
(not RIC directly). Compose branch is unchanged.

`handler.py` and `store.py` may import `jdparser.config` at module top **because**
bootstrap already exec'd a new interpreter. Tests call `handler_with_deps` and
never run bootstrap.

Telegram secret is loaded **inside** `load_telegram_credentials` (lazy boto3),
not via bootstrap.

### 4.2 Store (canonical signatures)

```python
class ScheduleStore(Protocol):
    def get_resume(self) -> tuple[bytes, str]: ...          # bytes, suffix e.g. ".pdf"
    def get_search_config(self) -> ScheduleSearchConfig: ...
    def hydrate_profiles(self, dest_dir: Path) -> int: ...  # count written
    def persist_profiles(self, src_dir: Path) -> int: ...
    def put_archived_run(self, rec: ArchivedRun) -> str: ...  # returns s3 key
    def get_day_lock(self, schedule_date: str) -> DayLock | None: ...
    def put_day_lock(self, lock: DayLock, *, create_only: bool) -> None: ...
    def get_notified(self) -> NotifiedSet: ...
    def put_notified(self, ns: NotifiedSet) -> None: ...
```

`create_only=True` maps to `If-None-Match: *`. Collision → `SCHEDULE_LOCK_HELD`.

### 4.3 Notify

```python
def is_new(job: dict[str, Any], ns: NotifiedSet) -> bool: ...
def format_header(schedule_date: str, new_count: int, qualified_count: int) -> str: ...
def format_zero(schedule_date: str, qualified_count: int) -> str: ...
def format_job(job: dict[str, Any]) -> str: ...
def chunk_jobs(header: str, job_texts: list[str], limit: int = TELEGRAM_CHUNK_CHARS) -> list[str]: ...
def send_message(text: str, *, token: str, chat_id: str, timeout_s: int = TELEGRAM_SEND_TIMEOUT_S) -> None: ...
def load_telegram_credentials(secret_arn: str | None = None) -> tuple[str, str]: ...
def notify_new_jobs(
    *,
    schedule_date: str,
    qualified_jobs: list[dict[str, Any]],
    ns: NotifiedSet,
    send: Callable[[str], None],
    put_notified: Callable[[NotifiedSet], None],
    now_iso: str,
) -> tuple[NotifiedSet, int]: ...
```

`job` is an `EvaluatedJob.model_dump()` dict (root SPEC §3.9).
Telegram jobs are **only** members of `qualified_jobs`, and
`qualified_jobs` is **only** `is_qualified(evaluated)` (root SPEC §7).
`status=="qualified"` is not sufficient. Screened-out jobs are not in
`evaluated_jobs` and are never sent.

`send_message` uses stdlib `urllib.request.urlopen(..., timeout=timeout_s)`
(total socket timeout). Non-2xx → `SCHEDULE_TELEGRAM_FAILED`.

### 4.4 Graph invoke (reuse, do not fork)

```python
from jdparser.graph.build import build_graph
from jdparser.graph.state import initial_state
# graph.invoke(initial_state(...), config={
#   "configurable": {"thread_id": run_id},
#   "max_concurrency": EVAL_FANOUT_CONCURRENCY,
# })
```

Partition evaluated jobs the same way `server.py` `_execute` does (copy, do not
import from `server.py`):

```python
# canonical — keep identical to packages/api/jdparser/server.py _execute
qualified = [e for e in evaluated if is_qualified(EvaluatedJob.model_validate(e))]
# Prefer final["qualified_jobs"] when invoke_graph returns graph state that
# already applied is_qualified (build_graph does). Tests that stub invoke_graph
# may omit it; then compute as above.
failures = [e for e in evaluated if e["status"] == "failed"]
failure_ids = {e["job_id"] for e in failures}
qualified_ids = {e["job_id"] for e in qualified}
rejected = [e for e in evaluated if e["job_id"] not in failure_ids and e["job_id"] not in qualified_ids]
```

`rejected` is the complement of `(qualified | failures)`, not a status-set
filter — a subgraph `status=="qualified"` job that fails `is_qualified()` lands
in `rejected` and is not Telegraphed.

Call `start_run_usage()` before `invoke` (root `server.py`). Stamp
`RunRecord.usage` on success and failure. Do not call `runs/store.py`;
`$JDPARSER_DATA_DIR/runs` may stay empty. Profiles: hydrate **replaces** files
in `dest_dir` for listed keys; persist uploads every `*.json` after invoke.
`persist_profiles` is **best-effort**: log `SCHEDULE_S3` and continue; resume,
day lock, archive, and notified puts are fatal.

### 4.5 Telegram Bot API

`POST https://api.telegram.org/bot{token}/sendMessage`

JSON body: `chat_id`, `text` (max `TELEGRAM_CHUNK_CHARS`),
`disable_web_page_preview: true`. No parse_mode (plain text). Token and chat
id from Secrets Manager JSON keys `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

Alarm relay uses the same secret and the same `sendMessage` shape. Recs and
alarms may interleave in one chat; recs prefix `jdparser {schedule_date}:`;
alarms prefix `jdparser alarm:`.

### 4.6 Error codes (this plane) and mapping

Root `/SPEC.md` §6.4 codes still apply inside the graph. This plane adds:

| Code | Raised by | Meaning |
|---|---|---|
| `SCHEDULE_RESUME_MISSING` | handler | `resume/current` not in bucket |
| `SCHEDULE_CONFIG_INVALID` | store | `config/search.json` present but not a valid `ScheduleSearchConfig` |
| `SCHEDULE_LOCK_HELD` | store | `If-None-Match` lost the race |
| `SCHEDULE_TELEGRAM_FAILED` | notify | non-2xx or transport error from Bot API |
| `SCHEDULE_SECRET_MISSING` | handler | secret JSON missing token or chat_id |
| `SCHEDULE_S3` | store | unexpected S3 error |

All are `JDParserError(code=...)`. Graph codes inside a completed run stay on
`RunRecord.errors` and do **not** fail the Lambda (same as the API path). A
fatal graph exception (`EVAL_COUNT_MISMATCH`, uncaught) fails the Lambda.

| Internal | Lambda platform | Telegram recs |
|---|---|---|
| `ScheduleResult.status=="completed"` | 200 / invoke success | header + jobs or heartbeat sent |
| `ScheduleResult.status=="skipped"` | 200 / invoke success | **no** recs message |
| `JDParserError` after lock | invoke error (raised); **not** retried (`LAMBDA_ASYNC_RETRIES=0`); may land on function DLQ | no recs (except prefix already sent) |
| CloudWatch alarm | n/a | relay message `jdparser alarm:…` |

---

## 5. Observable side effects

### 5.1 Telegram recs (new jobs)

Order, plain text:

1. Header: `jdparser {schedule_date}: {new_count} new / {qualified_count} qualified`
2. One message per new job, or concatenated until `TELEGRAM_CHUNK_CHARS`
   (IMPLEMENTATION_NOTIFY.md). Job block:

```
{title} @ {company}
{location}
{final_url}
{first met_requirements[].evidence_quote or "(no evidence quote)"}
```

### 5.2 Telegram recs (zero new)

Single message:

```
jdparser {schedule_date}: no new qualified jobs ({qualified_count} qualified, all previously sent)
```

When `qualified_count==0`: `jdparser {schedule_date}: no qualified jobs`.

### 5.3 S3 writes (this invoke)

At least: day lock `running` then `completed`/`failed`. On a non-skip run:
`runs/{schedule_date}/{run_id}.json`. On each successful job send: updated
`state/notified.json`. Profiles: best-effort persist after invoke.

### 5.4 Logs

Stdout one JSON object per phase, keys always present:

```json
{"phase":"lock","schedule_date":"2026-08-24","run_id":null,"ok":true}
{"phase":"done","schedule_date":"2026-08-24","run_id":"<uuid>","ok":true}
```

`phase` in `lock|resume|graph|archive|notify|done`. `run_id` is `null` until
assigned. Never log secret values.

---

## 6. Constants

| Name | Value | Unit / meaning |
|---|---|---|
| `SCHEDULE_TZ` | `"America/Chicago"` | IANA tz; 07:00 wall clock (CST/CDT) |
| `SCHEDULE_CRON_MON` | `cron({0,16,32,48} 7 ? * MON *)` | Monday last-week ticks, staggered 16 min |
| `SCHEDULE_CRON_WKDAY` | `cron({0,16,32,48} 7 ? * TUE-FRI *)` | Tue–Fri last-24h ticks, same stagger |
| `SCHEDULE_LOCK_STALE_S` | `900` | seconds; 15 min; **equal** to `LAMBDA_TIMEOUT_S`; age `>=` this → takeover |
| `LAMBDA_TIMEOUT_S` | `900` | seconds; Lambda max |
| `LAMBDA_MEMORY_MB` | `3008` | AWS Lambda memory_size (MB, not MiB) |
| `LAMBDA_EPHEMERAL_MB` | `2048` | AWS ephemeral `/tmp` MB — resume/profile scratch + Chromium user-data; browser **binary** is in the image |
| `LAMBDA_ARCH` | `arm64` | Graviton |
| `LAMBDA_RESERVED_CONCURRENCY` | `1` | count |
| `LAMBDA_ASYNC_RETRIES` | `0` | `aws_lambda_function_event_invoke_config.maximum_retry_attempts`; Scheduler owns invoke retries |
| `SCHEDULER_RETRY_ATTEMPTS` | `2` | additional Scheduler delivery attempts after the first **invoke** failure (not handler exceptions) |
| `SCHEDULER_EVENT_AGE_S` | `3600` | seconds; Scheduler retry event age |
| `SECRET_RECOVERY_DAYS` | `7` | days; Secrets Manager recovery window |
| `TELEGRAM_CHUNK_CHARS` | `3500` | Python `len(str)` on the UTF-8 text sent; Bot API cap is 4096 code points |
| `TELEGRAM_SEND_TIMEOUT_S` | `10` | seconds; `urlopen(..., timeout=)` |
| `JDPARSER_DATA_DIR` default (Lambda) | `/tmp/jdparser-data` | path; only writable dir |
| `NAME_PREFIX` default | `"jdparser"` | resource names |
| `LOG_RETENTION_DAYS` | `30` | days |
| `DLQ_RETENTION_S` | `1209600` | seconds; 14 days |
| `ENABLE_SCHEDULE` default | `false` | Terraform; persist in tfvars |
| `ENABLE_TELEGRAM_ALERTS` default | `false` | Terraform; persist in tfvars |

Secret ids: `{name_prefix}/runtime` (OpenRouter + Adzuna),
`{name_prefix}/telegram-alerts` (bot token + chat id).

Env vars on the search Lambda (names, never payloads):

| Env | Value |
|---|---|
| `SCHEDULE_BUCKET` | bucket name |
| `RUNTIME_SECRET_ARN` | runtime secret ARN |
| `TELEGRAM_SECRET_ARN` | telegram secret ARN |
| `JDPARSER_DATA_DIR` | `/tmp/jdparser-data` |
| `AWS_REGION` | set by Lambda |

Pins (this plane only; graph pins stay in `docs/IMPLEMENTATION.md`):

| Package | Pin | Why | Probed |
|---|---|---|---|
| `boto3` | `1.43.79` | S3 + Secrets Manager | PyPI 2026-08-24 |
| `awslambdaric` | `4.0.2` | RIC in custom image | PyPI 2026-08-24 |
| `hashicorp/aws` | `6.55.0` | Terraform AWS provider | binary lock; registry pin |
| `hashicorp/archive` | `2.8.0` | zip the relay | binary lock |
| `terraform` | `>= 1.5.0` | Scheduler resource | CLI constraint |
| `python` | `3.11` | existing image | `.python-version` |

`awslambdaric` is **Dockerfile-only** (Linux wheels). Do not add it to
`pyproject.toml` — it breaks `uv sync` on macOS. `boto3==1.43.79` **is** a
`pyproject.toml` runtime pin; `uv lock` updated in Phase 0.

Existing image base stays `python:3.11-slim-bookworm` + Playwright `1.60.0` +
Xvfb. No new Playwright major.

Validation: pins reachable as of 2026-08-24 (`boto3`, `awslambdaric` via PyPI).

Host tools (not Python/Terraform provider pins): AWS CLI v2, Docker with
`buildx` for `linux/arm64`, Terraform CLI `>= 1.5.0`. Relay zip uses the
**managed** `python3.11` Lambda runtime (AWS-provided boto3, not the 1.43.79
pin). Search image boto3 is 1.43.79.

---

## 7. Acceptance scenarios

- **§7.1 Seeded invoke.** After resume + telegram secret exist, a manual
  `lambda invoke` writes `runs/{schedule_date}/{run_id}.json` whose inner
  `run` validates as `RunRecord`, and Telegram receives §5.1 or §5.2.
- **§7.2 Same-day skip.** Second invoke the same `schedule_date` after
  `completed` returns `status=skipped`, does not write a second run object,
  does not send Telegram recs.
- **§7.3 Notify suppress.** A `job_id` present in `state/notified.json` is
  absent from Telegram even if it appears in that run's `qualified_jobs`.
- **§7.4 Display gate.** Every job in a Telegram recs message satisfies root
  `is_qualified()`. Failed / uncertain / screened-out jobs are never sent.
- **§7.5 Schedule gated.** `terraform plan` with default `enable_schedule=false`
  shows the Scheduler `state = DISABLED` (or equivalent). Enabling requires a
  persisted tfvars flag, not a one-shot `-var`.
- **§7.6 Compose intact.** `docker compose up` still serves FastAPI on 8000;
  `GET /api/health` returns `{"status":"ok"}`. The image entrypoint still
  starts Xvfb + uvicorn when `AWS_LAMBDA_RUNTIME_API` is unset.
- **§7.7 Resume missing.** Invoke with no `resume/current` raises
  `SCHEDULE_RESUME_MISSING`, day lock `failed`, no recs message.

Tier 3.5: not required. One tick per day; no intra-tick trajectory.

---

## 8. Out of scope

- Next.js / API Gateway / Telegram inbound webhook / bot commands.
- Multi-user, extra chats, extra resumes (one `resume/current`).
- SQL, DynamoDB, EFS, VPC, NAT.
- Second job source; country ≠ `ADZUNA_COUNTRY`.
- Enabling `enable_schedule` in the create-infra apply.
- Changing root `is_qualified()` or LLM prompts.
- Email / SMS. Alarm email is optional (`alarm_email` empty = none).

---

## 9. MVP-stubbed surfaces (normative)

| Surface | Stub behavior (MVP) | Eventual final form | File |
|---|---|---|---|
| LangGraph checkpointer | in-memory `MemorySaver` (lost on freeze) | S3 is the archive, not a tick resume | `graph/build.py` (unchanged) |
| Resume identity | one S3 object `resume/current` | per-user objects | `schedule/store.py` |
| Search config | operator-uploaded `config/search.json` | out of this plane (would need web/API) | `schedule/store.py` |
| Notified-set eviction | none | TTL / listing-expired | `schedule/store.py` |
| Telegram commands | none | `/run`, `/status` | — |
| Schedule state | Terraform `enable_schedule` default false | later apply flips tfvars | `infra/scheduler.tf` |
| Profile store (Lambda) | copy files S3 ↔ `$JDPARSER_DATA_DIR/profiles`; no new cache API | single store abstraction | `schedule/store.py` |
| Alarm relay | optional, default off; enabling is a later tfvars apply (toggling off **destroys** the zip function) | always-on | `infra/telegram_relay.tf` |

---

## 10. Locked decisions

1. **Timezone.** `America/Chicago` wall clock 07:00, not fixed UTC-6.
2. **One Lambda does search + recs.** Alarm relay is a separate optional zip.
3. **Image.** Same Dockerfile as compose; dual entrypoint. Handler, not uvicorn.
4. **Idempotency key.** America/Chicago `schedule_date`, not `run_id`.
5. **S3 authoritative; Telegram derived.**
6. **`is_qualified()` unchanged.** Recs are a subset of `qualified_jobs` minus
   `NotifiedSet`.
7. **Secrets.** Terraform envelope + `ignore_changes` on secret string; values
   via `put-secret-value` (RUNBOOK).
8. **Infra files live in one Terraform root.** Filename prefixes only; no
   `infra/modules` subfolder without a `module` block (would drop resources).
9. **Runtime secrets via `bootstrap.py` exec** before RIC loads `jdparser.config`.
10. **Lambda async retries = 0.** Day lock is the idempotency layer; Scheduler
    retries only invoke failures.
