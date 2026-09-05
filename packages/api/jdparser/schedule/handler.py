"""Scheduled search Lambda handler — SPEC §3.1, §3.3-lifecycle, §4.1, §4.4, §5.4."""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable
from datetime import date, datetime, timezone
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from jdparser.config import (
    DATA_DIR,
    EVAL_FANOUT_CONCURRENCY,
    JDParserError,
    PROFILES_DIR,
    RUNS_DIR,
    SCHEDULE_LOCK_STALE_S,
    SCHEDULE_TZ,
    UPLOADS_DIR,
    now_iso,
)
from jdparser.graph.build import build_graph
from jdparser.graph.nodes import is_qualified
from jdparser.graph.state import initial_state
from jdparser.llm.schemas import EvaluatedJob, RunRecord
from jdparser.llm.usage import start_run_usage
from jdparser.schedule.notify import load_telegram_credentials, notify_new_jobs, send_message
from jdparser.schedule.schemas import (
    ArchivedRun,
    DayLock,
    SEARCH_PRESETS,
    ScheduleResult,
    ScheduleSearchConfig,
)
from jdparser.schedule.store import ScheduleStore, s3_store_from_env

log = logging.getLogger(__name__)

InvokeGraph = Callable[[str, str, ScheduleSearchConfig], dict[str, Any]]


def _log_phase(
    phase: str,
    *,
    schedule_date: str,
    run_id: str | None,
    ok: bool,
) -> None:
    sys.stdout.write(
        json.dumps(
            {
                "phase": phase,
                "schedule_date": schedule_date,
                "run_id": run_id,
                "ok": ok,
            }
        )
        + "\n"
    )
    sys.stdout.flush()


def _aware_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _lock_age_s(now_iso_s: str, started_at: str) -> float:
    return (_aware_utc(now_iso_s) - _aware_utc(started_at)).total_seconds()


def schedule_date_from_event(event: dict[str, Any] | None) -> str:
    """SPEC §3.1. If event scheduled_time present (UTC ISO-8601, e.g. 2022-03-22T18:59:43Z):
    datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(ZoneInfo("America/Chicago")).date().isoformat()
    Do NOT slice [:10] off the UTC string.
    Else: datetime.now(ZoneInfo("America/Chicago")).date().isoformat().
    Validate YYYY-MM-DD via date.fromisoformat.
    """
    tz = ZoneInfo(SCHEDULE_TZ)
    raw = None if event is None else event.get("scheduled_time")
    if raw:
        value = (
            datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            .astimezone(tz)
            .date()
            .isoformat()
        )
    else:
        value = datetime.now(tz).date().isoformat()
    date.fromisoformat(value)
    return value


def search_preset_from_event(event: dict[str, Any] | None) -> str | None:
    """Named location preset. None = S3 config/search.json (or defaults)."""
    raw = None if event is None else event.get("search")
    if raw is None or raw == "":
        return None
    key = str(raw)
    if key not in SEARCH_PRESETS:
        raise JDParserError(code="SCHEDULE_CONFIG_INVALID", message=f"unknown search: {key}")
    return key


def search_config_from_event(event: dict[str, Any] | None) -> ScheduleSearchConfig | None:
    """Preset plus optional max_days_old override (1 = last 24h, 7 = last week)."""
    search = search_preset_from_event(event)
    if search is None:
        return None
    cfg = SEARCH_PRESETS[search]
    if event is None or "max_days_old" not in event:
        return cfg
    raw = event["max_days_old"]
    if raw is None or raw == "":
        return cfg.model_copy(update={"max_days_old": None})
    try:
        days = int(raw)
    except (TypeError, ValueError) as exc:
        raise JDParserError(
            code="SCHEDULE_CONFIG_INVALID",
            message=f"invalid max_days_old: {raw!r}",
        ) from exc
    return cfg.model_copy(update={"max_days_old": None if days <= 0 else days})


def partition_evaluated(
    evaluated: list[dict[str, Any]],
    qualified_from_graph: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Prefer qualified_from_graph when provided (build_graph already applied is_qualified).
    Else: qualified = [e for e in evaluated if is_qualified(EvaluatedJob.model_validate(e))]
    — you may call is_qualified(e) directly on dump dicts like server.py.
    failures = [e for e in evaluated if e["status"] == "failed"]
    rejected = complement of (qualified | failures). Return (qualified, failures, rejected).
    """
    if qualified_from_graph is not None:
        qualified = list(qualified_from_graph)
    else:
        qualified = [
            e
            for e in evaluated
            if is_qualified(EvaluatedJob.model_validate(e).model_dump())
        ]
    failures = [e for e in evaluated if e["status"] == "failed"]
    failure_ids = {e["job_id"] for e in failures}
    qualified_ids = {e["job_id"] for e in qualified}
    rejected = [
        e
        for e in evaluated
        if e["job_id"] not in failure_ids and e["job_id"] not in qualified_ids
    ]
    return qualified, failures, rejected


def build_run_record(
    *,
    run_id: str,
    final: dict[str, Any],
    error: str | None,
    usage: Any | None,
) -> RunRecord:
    """status completed if error is None else failed. Stamp now_iso() for created_at/updated_at.
    partition_evaluated(final.get('evaluated_jobs') or [], final.get('qualified_jobs')).
    Copy resume_cache_hit, resume_cache_key from resume_fingerprint, resume_profile, errors, screened_out.
    Stamp usage (dict from usage.as_dict() if object has it). user_id='local'.
    qualified/failures/rejected/errors as lists. error field set. Do not call runs/store.py.
    """
    stamp = now_iso()
    qualified, failures, rejected = partition_evaluated(
        list(final.get("evaluated_jobs") or []),
        final.get("qualified_jobs"),
    )
    usage_val: Any | None = usage
    as_dict = getattr(usage, "as_dict", None)
    if callable(as_dict):
        usage_val = as_dict()
    return RunRecord(
        run_id=run_id,
        user_id="local",
        status="completed" if error is None else "failed",
        created_at=stamp,
        updated_at=stamp,
        resume_cache_hit=final.get("resume_cache_hit"),
        resume_cache_key=final.get("resume_fingerprint"),
        resume_profile=final.get("resume_profile"),
        qualified_jobs=list(qualified),
        failures=list(failures),
        rejected=list(rejected),
        errors=list(final.get("errors") or []),
        screened_out=list(final.get("screened_out") or []),
        usage=usage_val,
        error=error,
    )


def _fail_lock(
    store: ScheduleStore,
    *,
    schedule_date: str,
    run_id: str,
    started_at: str,
    now_iso_s: str,
    error: str,
    run_s3_key: str | None,
    search: str | None = None,
) -> None:
    store.put_day_lock(
        DayLock(
            schedule_date=schedule_date,
            status="failed",
            run_id=run_id,
            started_at=started_at,
            updated_at=now_iso_s,
            run_s3_key=run_s3_key,
            error=error,
        ),
        create_only=False,
        search=search,
    )


def run_scheduled_search(
    *,
    event: dict[str, Any],
    store: ScheduleStore,
    send: Callable[[str], None],
    invoke_graph: InvokeGraph,
    now_iso: str,
) -> ScheduleResult:
    schedule_date = schedule_date_from_event(event)
    search = search_preset_from_event(event)
    lock = store.get_day_lock(schedule_date, search=search)

    if lock is not None and lock.status == "completed":
        _log_phase("lock", schedule_date=schedule_date, run_id=lock.run_id, ok=True)
        _log_phase("done", schedule_date=schedule_date, run_id=lock.run_id, ok=True)
        return ScheduleResult(
            ok=True,
            status="skipped",
            skip_reason="already_completed",
            schedule_date=schedule_date,
            run_id=lock.run_id,
        )

    if lock is not None and lock.status == "running":
        age = _lock_age_s(now_iso, lock.started_at)
        if age < SCHEDULE_LOCK_STALE_S:
            _log_phase("lock", schedule_date=schedule_date, run_id=lock.run_id, ok=True)
            _log_phase("done", schedule_date=schedule_date, run_id=lock.run_id, ok=True)
            return ScheduleResult(
                ok=True,
                status="skipped",
                skip_reason="in_flight",
                schedule_date=schedule_date,
                run_id=lock.run_id,
            )

    run_id = str(uuid4())
    started_at = now_iso
    try:
        store.put_day_lock(
            DayLock(
                schedule_date=schedule_date,
                status="running",
                run_id=run_id,
                started_at=started_at,
                updated_at=now_iso,
            ),
            create_only=(lock is None),
            search=search,
        )
    except JDParserError as exc:
        if exc.code == "SCHEDULE_LOCK_HELD":
            _log_phase("lock", schedule_date=schedule_date, run_id=run_id, ok=True)
            _log_phase("done", schedule_date=schedule_date, run_id=run_id, ok=True)
            return ScheduleResult(
                ok=True,
                status="skipped",
                skip_reason="lock_held",
                schedule_date=schedule_date,
                run_id=run_id,
            )
        raise

    _log_phase("lock", schedule_date=schedule_date, run_id=run_id, ok=True)

    key: str | None = None
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

        try:
            body, suffix = store.get_resume()
        except JDParserError:
            _log_phase("resume", schedule_date=schedule_date, run_id=run_id, ok=False)
            raise
        resume_path = UPLOADS_DIR / f"{run_id}{suffix}"
        resume_path.write_bytes(body)
        _log_phase("resume", schedule_date=schedule_date, run_id=run_id, ok=True)

        cfg = search_config_from_event(event)
        if cfg is None:
            cfg = store.get_search_config()
        store.hydrate_profiles(PROFILES_DIR)

        usage = start_run_usage()
        invoke_exc: BaseException | None = None
        try:
            final = invoke_graph(run_id, str(resume_path), cfg)
        except Exception as exc:
            invoke_exc = exc
            final = {}
            _log_phase("graph", schedule_date=schedule_date, run_id=run_id, ok=False)
        else:
            _log_phase("graph", schedule_date=schedule_date, run_id=run_id, ok=True)

        error_s: str | None = None if invoke_exc is None else str(invoke_exc)
        rec = build_run_record(
            run_id=run_id, final=final, error=error_s, usage=usage
        )

        try:
            store.persist_profiles(PROFILES_DIR)
        except JDParserError as exc:
            log.warning("persist_profiles: %s", exc)
        except Exception as exc:
            log.warning("persist_profiles: %s", exc)

        run_dump = rec.model_dump()
        RunRecord.model_validate(run_dump)
        try:
            key = store.put_archived_run(
                ArchivedRun(
                    schedule_date=schedule_date,
                    search_config=cfg,
                    run=run_dump,
                )
            )
        except Exception:
            _log_phase("archive", schedule_date=schedule_date, run_id=run_id, ok=False)
            raise
        _log_phase("archive", schedule_date=schedule_date, run_id=run_id, ok=True)

        if invoke_exc is not None:
            raise invoke_exc

        try:
            _ns, new_notified = notify_new_jobs(
                schedule_date=schedule_date,
                qualified_jobs=list(rec.qualified_jobs),
                ns=store.get_notified(),
                send=send,
                put_notified=store.put_notified,
                now_iso=now_iso,
            )
        except Exception:
            _log_phase("notify", schedule_date=schedule_date, run_id=run_id, ok=False)
            raise
        _log_phase("notify", schedule_date=schedule_date, run_id=run_id, ok=True)

        store.put_day_lock(
            DayLock(
                schedule_date=schedule_date,
                status="completed",
                run_id=run_id,
                started_at=started_at,
                updated_at=now_iso,
                run_s3_key=key,
                new_notified=new_notified,
            ),
            create_only=False,
            search=search,
        )
        _log_phase("done", schedule_date=schedule_date, run_id=run_id, ok=True)
        return ScheduleResult(
            ok=True,
            status="completed",
            schedule_date=schedule_date,
            run_id=run_id,
            run_s3_key=key,
            new_notified=new_notified,
        )
    except Exception as exc:
        try:
            _fail_lock(
                store,
                schedule_date=schedule_date,
                run_id=run_id,
                started_at=started_at,
                now_iso_s=now_iso,
                error=str(exc),
                run_s3_key=key,
                search=search,
            )
        except Exception:
            log.exception("failed to write failed day lock")
        raise


def handler_with_deps(
    *,
    event: dict[str, Any],
    store: ScheduleStore,
    send: Callable[[str], None],
    invoke_graph: InvokeGraph,
    now_iso: str,
) -> ScheduleResult:
    """Test entry. Never reads env. Same kwargs as run_scheduled_search."""
    return run_scheduled_search(
        event=event,
        store=store,
        send=send,
        invoke_graph=invoke_graph,
        now_iso=now_iso,
    )


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Production: s3_store_from_env, load_telegram_credentials, send_message
    closure, build_graph().invoke. Runtime API keys already in environ
    (bootstrap). Raises on fail. Returned dict always ok=True."""
    del context
    store = s3_store_from_env()
    token, chat_id = load_telegram_credentials()

    def send(text: str) -> None:
        send_message(text, token=token, chat_id=chat_id)

    def invoke_graph(run_id: str, path: str, cfg: ScheduleSearchConfig) -> dict[str, Any]:
        max_days_old = cfg.max_days_old
        if max_days_old is None or max_days_old <= 0:
            max_days_old = None
        graph = build_graph()
        final: dict[str, Any] = graph.invoke(
            initial_state(
                run_id,
                resume_file_path=path,
                search_locations=cfg.locations,
                broaden_search=cfg.broaden_search,
                max_days_old=max_days_old,
                include_agencies=cfg.include_agencies,
            ),
            config={
                "configurable": {"thread_id": run_id},
                "max_concurrency": EVAL_FANOUT_CONCURRENCY,
            },
        )
        return final

    result = run_scheduled_search(
        event=event or {},
        store=store,
        send=send,
        invoke_graph=invoke_graph,
        now_iso=now_iso(),
    )
    return result.model_dump()
