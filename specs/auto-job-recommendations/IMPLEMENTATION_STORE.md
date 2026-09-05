# IMPLEMENTATION_STORE — S3 archive

> Owns S3 keys and the `ScheduleStore` protocol. References: SPEC §3.2–§3.8,
> §4.2, §6.

## Purpose

Replace the Lambda's ephemeral disk with a durable bucket. Local
`cache/store.py` / `runs/store.py` stay the compose path. This module hydrates
and persists around a graph invoke; it does not rewrite those modules.

## Files this area owns

- `packages/api/jdparser/schedule/__init__.py`
- `packages/api/jdparser/schedule/schemas.py`
- `packages/api/jdparser/schedule/store.py`
- `packages/api/tests/test_schedule_store.py`

Must NOT edit `graph/`, `server.py`, `cache/store.py`, `infra/`.

## `schemas.py`

Canonical models: `ScheduleSearchConfig`, `DayLock`, `DayLockStatus`,
`NotifiedJob`, `NotifiedSet`, `ArchivedRun`, `ScheduleResult`,
`ScheduleStatus` — field-for-field SPEC §3.2–§3.5, §3.7.

```python
from pydantic import BaseModel, Field
```

`NotifiedSet.jobs` / `.urls` default to empty dicts. `DayLock.run_s3_key`,
`error`, `new_notified` default `None`.

## Keys (bucket = `SCHEDULE_BUCKET`)

| Key | Body |
|---|---|
| `resume/current` | raw bytes |
| `config/search.json` | `ScheduleSearchConfig` |
| `profiles/{cache_key}.json` | `StoredResumeProfile` |
| `runs/{schedule_date}/{run_id}.json` | `ArchivedRun` |
| `state/day/{schedule_date}.json` | `DayLock` |
| `state/notified.json` | `NotifiedSet` |

`schedule_date` / `cache_key` / `run_id` are path-safe (`[0-9a-f-]` / ISO date).
Do not interpolate unsanitized event strings into keys; validate
`schedule_date` with `datetime.date.fromisoformat`.

## `store.py`

```python
def parse_resume_suffix(body: bytes) -> str:
    """SPEC §3.6 magic-byte sniff: %PDF → '.pdf'; ZIP with word/ in first 4096 → '.docx'; else '.txt'."""

class S3ScheduleStore:
    def __init__(self, client: Any, bucket: str) -> None: ...
    def get_resume(self) -> tuple[bytes, str]: ...
    def get_search_config(self) -> ScheduleSearchConfig: ...
    def hydrate_profiles(self, dest_dir: Path) -> int: ...
    def persist_profiles(self, src_dir: Path) -> int: ...
    def put_archived_run(self, rec: ArchivedRun) -> str: ...
    def get_day_lock(self, schedule_date: str) -> DayLock | None: ...
    def put_day_lock(self, lock: DayLock, *, create_only: bool) -> None: ...
    def get_notified(self) -> NotifiedSet: ...
    def put_notified(self, ns: NotifiedSet) -> None: ...
```

`client` is a boto3 S3 client (injected). Production builder:

```python
def s3_store_from_env() -> S3ScheduleStore:
    import boto3
    bucket = os.environ["SCHEDULE_BUCKET"]
    return S3ScheduleStore(boto3.client("s3"), bucket)
```

### `get_resume`

`get_object(Bucket, Key="resume/current")`. `NoSuchKey` / 404 →
`JDParserError("SCHEDULE_RESUME_MISSING")`. Suffix via `parse_resume_suffix(body)`
(SPEC §3.6). Ignore Content-Type.

### `get_search_config`

Missing key → `ScheduleSearchConfig()`. Present: `model_validate_json`;
`ValidationError` → `JDParserError("SCHEDULE_CONFIG_INVALID")`.

### `hydrate_profiles` / `persist_profiles`

`list_objects_v2` prefix `profiles/`. Write each object to
`dest_dir / filename` (filename = key after prefix). `persist_profiles` uploads
every `src_dir.glob("*.json")` to `profiles/{name}`. Return counts. Pagination
required (`ContinuationToken`).

### `put_archived_run`

Key `runs/{rec.schedule_date}/{rec.run["run_id"]}.json`. Extra args:
`IfNoneMatch="*"` (botocore parameter name `IfNoneMatch` on `put_object`;
HTTP 412 on conflict). Body `rec.model_dump_json()`. Return the key.

### `put_day_lock`

`create_only=True`: `IfNoneMatch="*"`. Precondition failed (HTTP 412) →
`JDParserError("SCHEDULE_LOCK_HELD")`. `create_only=False`: ordinary put
(overwrite).

### `get_notified`

Missing → `NotifiedSet(jobs={}, urls={}, updated_at=now_iso())`.

Any other S3 client exception → `JDParserError("SCHEDULE_S3", message=...)`.

## Fake for tests

`tests/test_schedule_store.py` uses an in-memory dict store implementing the
same methods (no moto, no network). Cover: missing resume; default config;
invalid config JSON; day-lock 412; notified missing=empty; archive key shape;
hydrate/persist round-trip of one profile JSON.

## Done when

`uv run pytest tests/test_schedule_store.py` green; `mypy --strict` clean on
`jdparser/schedule/schemas.py` and `store.py`.
