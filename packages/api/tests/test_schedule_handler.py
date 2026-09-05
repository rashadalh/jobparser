"""Scheduled handler — SPEC §3.1, §3.3-lifecycle, §7.2–§7.4, §7.7; IMPLEMENTATION_HANDLER."""

from __future__ import annotations

import copy
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from graph_harness import _valid_ej
from jdparser.config import JDParserError, SCHEDULE_LOCK_STALE_S, SCHEDULE_TZ
from jdparser.graph.nodes import is_qualified
from jdparser.llm.schemas import RunRecord
from jdparser.schedule import handler as handler_mod
from jdparser.schedule.bootstrap import load_runtime_secrets, ric_python
from jdparser.schedule.handler import (
    handler_with_deps,
    partition_evaluated,
    schedule_date_from_event,
)
from jdparser.schedule.schemas import (
    ArchivedRun,
    DayLock,
    NotifiedJob,
    NotifiedSet,
    ScheduleSearchConfig,
)
from jdparser.schedule.store import ScheduleStore

NOW = "2026-08-25T12:00:00+00:00"
EVENT: dict[str, Any] = {"scheduled_time": "2026-08-25T12:00:00Z"}
SCHEDULE_DATE = "2026-08-25"


class FakeStore:
    """Dict-backed ScheduleStore for handler tests (no AWS)."""

    def __init__(self) -> None:
        self.locks: dict[str, DayLock] = {}
        self.resume: tuple[bytes, str] | None = (b"%PDF-1.4\nresume", ".pdf")
        self.config = ScheduleSearchConfig()
        self.archives: dict[str, ArchivedRun] = {}
        self.notified = NotifiedSet(
            jobs={}, urls={}, updated_at="1970-01-01T00:00:00+00:00"
        )
        self.hydrate_dirs: list[Path] = []
        self.persist_dirs: list[Path] = []

    def get_resume(self) -> tuple[bytes, str]:
        if self.resume is None:
            raise JDParserError(code="SCHEDULE_RESUME_MISSING")
        return self.resume

    def get_search_config(self) -> ScheduleSearchConfig:
        return self.config

    def hydrate_profiles(self, dest_dir: Path) -> int:
        dest_dir.mkdir(parents=True, exist_ok=True)
        self.hydrate_dirs.append(dest_dir)
        return 0

    def persist_profiles(self, src_dir: Path) -> int:
        self.persist_dirs.append(src_dir)
        return 0

    def put_archived_run(self, rec: ArchivedRun) -> str:
        run_id = str(rec.run["run_id"])
        key = f"runs/{rec.schedule_date}/{run_id}.json"
        if key in self.archives:
            raise JDParserError(code="SCHEDULE_S3", message="archive key exists")
        self.archives[key] = rec
        return key

    def get_day_lock(self, schedule_date: str) -> DayLock | None:
        return self.locks.get(schedule_date)

    def put_day_lock(self, lock: DayLock, *, create_only: bool) -> None:
        if create_only and lock.schedule_date in self.locks:
            raise JDParserError(code="SCHEDULE_LOCK_HELD")
        self.locks[lock.schedule_date] = lock

    def get_notified(self) -> NotifiedSet:
        return self.notified

    def put_notified(self, ns: NotifiedSet) -> None:
        self.notified = ns


def _qualified_subset(evaluated: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in evaluated if is_qualified(e)]


def _stub_final(
    evaluated: list[dict[str, Any]] | None = None,
    *,
    include_qualified: bool = True,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    jobs = evaluated if evaluated is not None else [_valid_ej()]
    out: dict[str, Any] = {
        "evaluated_jobs": jobs,
        "resume_cache_hit": False,
        "resume_fingerprint": "fp-1",
        "resume_profile": {"roles": ["engineer"]},
        "errors": [],
        "screened_out": [],
    }
    if include_qualified:
        out["qualified_jobs"] = _qualified_subset(jobs)
    if extra:
        out.update(extra)
    return out


class _Recorder:
    def __init__(self, final: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, str, ScheduleSearchConfig]] = []
        self.final = final if final is not None else _stub_final()

    def __call__(
        self, run_id: str, path: str, cfg: ScheduleSearchConfig
    ) -> dict[str, Any]:
        self.calls.append((run_id, path, cfg))
        return copy.deepcopy(self.final)


@pytest.fixture
def patched_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(handler_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(handler_mod, "PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr(handler_mod, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(handler_mod, "RUNS_DIR", tmp_path / "runs")
    return tmp_path


def _run(
    *,
    store: ScheduleStore,
    send: list[str],
    invoke: _Recorder,
    event: dict[str, Any] = EVENT,
    now_iso: str = NOW,
) -> Any:
    return handler_with_deps(
        event=event,
        store=store,
        send=send.append,
        invoke_graph=invoke,
        now_iso=now_iso,
    )


def test_schedule_date_from_event_chicago_not_utc_slice() -> None:
    """UTC 2022-03-23T04:00:00Z is still 2022-03-22 in Chicago (CDT). [:10] would be wrong."""
    assert (
        schedule_date_from_event({"scheduled_time": "2022-03-23T04:00:00Z"})
        == "2022-03-22"
    )
    assert (
        schedule_date_from_event({"scheduled_time": "2022-03-22T18:59:43Z"})
        == "2022-03-22"
    )
    date.fromisoformat(schedule_date_from_event({}))
    assert ZoneInfo(SCHEDULE_TZ).key == "America/Chicago"


def test_partition_evaluated_fallback_without_qualified_jobs() -> None:
    good = _valid_ej()
    failed = {**copy.deepcopy(good), "job_id": "job-fail", "status": "failed", "title": "F"}
    fake_q = copy.deepcopy(good)
    fake_q["job_id"] = "job-fake"
    fake_q["judgment"]["thematic_fit"] = False
    qualified, failures, rejected = partition_evaluated([good, failed, fake_q])
    assert [j["job_id"] for j in qualified] == ["job-0"]
    assert [j["job_id"] for j in failures] == ["job-fail"]
    assert [j["job_id"] for j in rejected] == ["job-fake"]


def test_first_run_completed_archives_and_notifies(patched_dirs: Path) -> None:
    store = FakeStore()
    sent: list[str] = []
    invoke = _Recorder()
    result = _run(store=store, send=sent, invoke=invoke)
    assert result.ok is True
    assert result.status == "completed"
    assert result.schedule_date == SCHEDULE_DATE
    assert result.run_id
    assert result.run_s3_key == f"runs/{SCHEDULE_DATE}/{result.run_id}.json"
    assert result.run_s3_key in store.archives
    inner = RunRecord.model_validate(store.archives[result.run_s3_key].run)
    assert inner.status == "completed"
    assert inner.user_id == "local"
    assert inner.run_id == result.run_id
    assert sent
    lock = store.get_day_lock(SCHEDULE_DATE)
    assert lock is not None
    assert lock.status == "completed"
    assert lock.run_s3_key == result.run_s3_key
    assert len(invoke.calls) == 1
    resume_path = Path(invoke.calls[0][1])
    assert resume_path.name == f"{result.run_id}.pdf"
    assert resume_path.exists()


def test_same_day_skip_no_second_invoke_or_archive(patched_dirs: Path) -> None:
    """SPEC §7.2."""
    store = FakeStore()
    sent: list[str] = []
    invoke = _Recorder()
    first = _run(store=store, send=sent, invoke=invoke)
    assert first.status == "completed"
    n_sent = len(sent)
    n_invoke = len(invoke.calls)
    n_arch = len(store.archives)
    second = _run(store=store, send=sent, invoke=invoke)
    assert second.status == "skipped"
    assert second.skip_reason == "already_completed"
    assert second.ok is True
    assert second.schedule_date == SCHEDULE_DATE
    assert second.run_id == first.run_id
    assert len(invoke.calls) == n_invoke
    assert len(sent) == n_sent
    assert len(store.archives) == n_arch


def test_notify_suppress_preseeded_job_id(patched_dirs: Path) -> None:
    """SPEC §7.3: job_id already in NotifiedSet is absent from Telegram."""
    good = _valid_ej()
    other = copy.deepcopy(good)
    other["job_id"] = "job-new"
    other["title"] = "Brand New Role"
    other["final_url"] = "https://employer.example/new"
    store = FakeStore()
    store.notified = NotifiedSet(
        jobs={
            good["job_id"]: NotifiedJob(
                job_id=good["job_id"],
                final_url=good["final_url"],
                title=good["title"],
                company=good["company"],
                first_notified_at="2026-08-01T00:00:00+00:00",
                schedule_date="2026-08-01",
            )
        },
        urls={good["final_url"]: good["job_id"]},
        updated_at="2026-08-01T00:00:00+00:00",
    )
    sent: list[str] = []
    invoke = _Recorder(_stub_final([good, other]))
    _run(store=store, send=sent, invoke=invoke)
    blob = "\n".join(sent)
    assert good["title"] not in blob
    assert other["title"] in blob


def test_display_gate_only_is_qualified_sent(patched_dirs: Path) -> None:
    """SPEC §7.4: failed / uncertain / qualified-but-not-is_qualified titles absent."""
    good = _valid_ej()
    failed = copy.deepcopy(good)
    failed["job_id"] = "job-fail"
    failed["title"] = "Failed Title"
    failed["status"] = "failed"
    uncertain = copy.deepcopy(good)
    uncertain["job_id"] = "job-unc"
    uncertain["title"] = "Uncertain Title"
    uncertain["status"] = "uncertain"
    fake_q = copy.deepcopy(good)
    fake_q["job_id"] = "job-fake"
    fake_q["title"] = "Thematic Miss"
    fake_q["judgment"]["thematic_fit"] = False
    screened_title = "Screened Out Title"
    sent: list[str] = []
    invoke = _Recorder(
        _stub_final(
            [good, failed, uncertain, fake_q],
            extra={
                "screened_out": [
                    {"job_id": "job-screen", "title": screened_title},
                ]
            },
        )
    )
    _run(store=FakeStore(), send=sent, invoke=invoke)
    blob = "\n".join(sent)
    assert good["title"] in blob
    assert failed["title"] not in blob
    assert uncertain["title"] not in blob
    assert fake_q["title"] not in blob
    assert screened_title not in blob


def test_resume_missing_fails_lock_no_invoke_no_send(patched_dirs: Path) -> None:
    """SPEC §7.7."""
    store = FakeStore()
    store.resume = None
    sent: list[str] = []
    invoke = _Recorder()
    with pytest.raises(JDParserError) as ei:
        _run(store=store, send=sent, invoke=invoke)
    assert ei.value.code == "SCHEDULE_RESUME_MISSING"
    assert invoke.calls == []
    assert sent == []
    lock = store.get_day_lock(SCHEDULE_DATE)
    assert lock is not None
    assert lock.status == "failed"
    assert store.archives == {}


def test_in_flight_lock_younger_than_stale_skipped(patched_dirs: Path) -> None:
    store = FakeStore()
    started = (
        datetime.fromisoformat(NOW) - timedelta(seconds=100)
    ).isoformat()
    store.locks[SCHEDULE_DATE] = DayLock(
        schedule_date=SCHEDULE_DATE,
        status="running",
        run_id="in-flight",
        started_at=started,
        updated_at=started,
    )
    sent: list[str] = []
    invoke = _Recorder()
    result = _run(store=store, send=sent, invoke=invoke)
    assert result.status == "skipped"
    assert result.skip_reason == "in_flight"
    assert result.run_id == "in-flight"
    assert invoke.calls == []
    assert sent == []
    assert store.archives == {}
    assert store.locks[SCHEDULE_DATE].run_id == "in-flight"


def test_stale_running_lock_takeover_invokes(patched_dirs: Path) -> None:
    store = FakeStore()
    started = (
        datetime.fromisoformat(NOW) - timedelta(seconds=SCHEDULE_LOCK_STALE_S + 1)
    ).isoformat()
    store.locks[SCHEDULE_DATE] = DayLock(
        schedule_date=SCHEDULE_DATE,
        status="running",
        run_id="stale-run",
        started_at=started,
        updated_at=started,
    )
    sent: list[str] = []
    invoke = _Recorder()
    result = _run(store=store, send=sent, invoke=invoke)
    assert result.status == "completed"
    assert len(invoke.calls) == 1
    assert result.run_id != "stale-run"
    lock = store.get_day_lock(SCHEDULE_DATE)
    assert lock is not None
    assert lock.status == "completed"
    assert lock.run_id == result.run_id


def test_load_runtime_secrets_maps_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNTIME_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:1:secret:rt")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    monkeypatch.delenv("ADZUNA_APP_KEY", raising=False)

    class _Client:
        def get_secret_value(self, SecretId: str) -> dict[str, str]:
            assert SecretId == "arn:aws:secretsmanager:us-east-1:1:secret:rt"
            return {
                "SecretString": json.dumps(
                    {
                        "OPENROUTER_API_KEY": "or-key",
                        "ADZUNA_APP_ID": "adz-id",
                        "ADZUNA_APP_KEY": "adz-key",
                    }
                )
            }

    load_runtime_secrets(client=_Client())
    assert os.environ["OPENROUTER_API_KEY"] == "or-key"
    assert os.environ["ADZUNA_APP_ID"] == "adz-id"
    assert os.environ["ADZUNA_APP_KEY"] == "adz-key"


def test_ric_python_is_venv_bin_not_bare_name() -> None:
    path = ric_python()
    assert path.endswith("/bin/python")
    assert Path(path).name == "python"


def test_load_runtime_secrets_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNTIME_SECRET_ARN", "arn:secret")

    class _Client:
        def get_secret_value(self, SecretId: str) -> dict[str, str]:
            return {"SecretString": json.dumps({"OPENROUTER_API_KEY": "only"})}

    with pytest.raises(JDParserError) as ei:
        load_runtime_secrets(client=_Client())
    assert ei.value.code == "SCHEDULE_SECRET_MISSING"


def test_equal_stale_age_is_takeover_not_in_flight(patched_dirs: Path) -> None:
    """Age equal to SCHEDULE_LOCK_STALE_S is not in-flight (SPEC §3.3-lifecycle)."""
    store = FakeStore()
    started = (
        datetime.fromisoformat(NOW) - timedelta(seconds=SCHEDULE_LOCK_STALE_S)
    ).isoformat()
    store.locks[SCHEDULE_DATE] = DayLock(
        schedule_date=SCHEDULE_DATE,
        status="running",
        run_id="edge-run",
        started_at=started,
        updated_at=started,
    )
    invoke = _Recorder()
    result = _run(store=store, send=[], invoke=invoke)
    assert result.status == "completed"
    assert len(invoke.calls) == 1


def test_fake_store_is_schedule_store() -> None:
    store: ScheduleStore = FakeStore()
    assert store.get_search_config().broaden_search is True


def test_schedule_date_uses_chicago_tz_not_utc_prefix() -> None:
    utc = datetime(2022, 3, 23, 4, 0, 0, tzinfo=timezone.utc)
    chicago = utc.astimezone(ZoneInfo("America/Chicago")).date().isoformat()
    assert chicago == "2022-03-22"
    assert utc.date().isoformat() == "2022-03-23"
