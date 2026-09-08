"""Tests for schedule/store S3 archive I/O (SPEC §3.2–§3.8, §4.2).

In-memory dict store plus a dict-backed fake boto3 client (no moto, no network).
"""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError

from builders import stored_profile
from jdparser.config import JDParserError
from jdparser.schedule.schemas import (
    ArchivedRun,
    DayLock,
    NotifiedSet,
    ScheduleSearchConfig,
)
from jdparser.schedule.store import (
    CONFIG_KEY,
    NOTIFIED_KEY,
    RESUME_KEY,
    S3ScheduleStore,
    ScheduleStore,
    day_lock_key,
    parse_resume_suffix,
    resume_object_key,
    s3_store_from_env,
)


def _client_error(code: str, status: int, operation: str) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": code},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        operation,
    )


def _day_lock(schedule_date: str = "2026-08-25", run_id: str = "run-1") -> DayLock:
    return DayLock(
        schedule_date=schedule_date,
        status="running",
        run_id=run_id,
        started_at="2026-08-25T12:00:00+00:00",
        updated_at="2026-08-25T12:00:00+00:00",
    )


def _archived_run(
    schedule_date: str = "2026-08-25",
    run_id: str = "abc-123",
) -> ArchivedRun:
    return ArchivedRun(
        schedule_date=schedule_date,
        search_config=ScheduleSearchConfig(),
        run={"run_id": run_id},
    )


class FakeS3Client:
    """Dict-backed boto3 S3 client stub: IfNoneMatch 412, NoSuchKey, list pagination."""

    def __init__(self, page_size: int = 1000) -> None:
        self.objects: dict[str, bytes] = {}
        self.page_size = page_size
        self.list_calls: list[dict[str, Any]] = []

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        key = kwargs["Key"]
        if key not in self.objects:
            raise _client_error("NoSuchKey", 404, "GetObject")
        return {"Body": io.BytesIO(self.objects[key]), "ContentType": "binary/octet-stream"}

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        key = kwargs["Key"]
        body = kwargs["Body"]
        data = body.encode("utf-8") if isinstance(body, str) else bytes(body)
        if kwargs.get("IfNoneMatch") == "*" and key in self.objects:
            raise _client_error("PreconditionFailed", 412, "PutObject")
        self.objects[key] = data
        return {}

    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
        self.list_calls.append(dict(kwargs))
        prefix = kwargs.get("Prefix", "")
        keys = sorted(k for k in self.objects if k.startswith(prefix))
        token = kwargs.get("ContinuationToken")
        start = int(token) if token else 0
        page = keys[start : start + self.page_size]
        next_start = start + self.page_size
        truncated = next_start < len(keys)
        result: dict[str, Any] = {"IsTruncated": truncated}
        if page:
            result["Contents"] = [{"Key": k} for k in page]
        if truncated:
            result["NextContinuationToken"] = str(next_start)
        return result


class MemoryScheduleStore:
    """In-memory dict store implementing the same methods as S3ScheduleStore."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def get_resume(self, key: str = RESUME_KEY) -> tuple[bytes, str]:
        body = self.objects.get(key)
        if body is None:
            raise JDParserError(code="SCHEDULE_RESUME_MISSING")
        return body, parse_resume_suffix(body)

    def get_search_config(self) -> ScheduleSearchConfig:
        body = self.objects.get(CONFIG_KEY)
        if body is None:
            return ScheduleSearchConfig()
        try:
            return ScheduleSearchConfig.model_validate_json(body)
        except Exception as exc:
            raise JDParserError(code="SCHEDULE_CONFIG_INVALID", message=str(exc)) from exc

    def hydrate_profiles(self, dest_dir: Path) -> int:
        dest_dir.mkdir(parents=True, exist_ok=True)
        count = 0
        prefix = "profiles/"
        for key, data in self.objects.items():
            if not key.startswith(prefix):
                continue
            rel = key[len(prefix) :]
            if not rel:
                continue
            out = dest_dir / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data)
            count += 1
        return count

    def persist_profiles(self, src_dir: Path) -> int:
        count = 0
        for path in src_dir.glob("*.json"):
            self.objects[f"profiles/{path.name}"] = path.read_bytes()
            count += 1
        return count

    def put_archived_run(self, rec: ArchivedRun) -> str:
        date.fromisoformat(rec.schedule_date)
        key = f"runs/{rec.schedule_date}/{rec.run['run_id']}.json"
        if key in self.objects:
            raise JDParserError(code="SCHEDULE_S3", message="archive key exists")
        self.objects[key] = rec.model_dump_json().encode("utf-8")
        return key

    def get_day_lock(
        self, schedule_date: str, search: str | None = None
    ) -> DayLock | None:
        body = self.objects.get(day_lock_key(schedule_date, search))
        if body is None:
            return None
        return DayLock.model_validate_json(body)

    def put_day_lock(
        self, lock: DayLock, *, create_only: bool, search: str | None = None
    ) -> None:
        key = day_lock_key(lock.schedule_date, search)
        if create_only and key in self.objects:
            raise JDParserError(code="SCHEDULE_LOCK_HELD")
        self.objects[key] = lock.model_dump_json().encode("utf-8")

    def get_notified(self) -> NotifiedSet:
        body = self.objects.get(NOTIFIED_KEY)
        if body is None:
            return NotifiedSet(jobs={}, urls={}, updated_at="1970-01-01T00:00:00+00:00")
        return NotifiedSet.model_validate_json(body)

    def put_notified(self, ns: NotifiedSet) -> None:
        self.objects[NOTIFIED_KEY] = ns.model_dump_json().encode("utf-8")


@pytest.fixture(params=["memory", "s3"])
def store_and_objs(request: pytest.FixtureRequest) -> tuple[ScheduleStore, dict[str, bytes]]:
    if request.param == "memory":
        mem = MemoryScheduleStore()
        return mem, mem.objects
    client = FakeS3Client()
    return S3ScheduleStore(client, "test-bucket"), client.objects


def test_parse_resume_suffix_pdf() -> None:
    assert parse_resume_suffix(b"%PDF-1.4\n%") == ".pdf"


def test_parse_resume_suffix_docx() -> None:
    body = b"PK\x03\x04" + b"x" * 20 + b"word/document.xml"
    assert parse_resume_suffix(body) == ".docx"


def test_parse_resume_suffix_pk_without_word_is_txt() -> None:
    assert parse_resume_suffix(b"PK\x03\x04not-a-docx") == ".txt"


def test_parse_resume_suffix_else_txt() -> None:
    assert parse_resume_suffix(b"plain resume text") == ".txt"
    assert parse_resume_suffix(b"") == ".txt"


def test_missing_resume_raises(store_and_objs: tuple[ScheduleStore, dict[str, bytes]]) -> None:
    store, _ = store_and_objs
    with pytest.raises(JDParserError) as ei:
        store.get_resume()
    assert ei.value.code == "SCHEDULE_RESUME_MISSING"


def test_get_resume_sniffs_pdf_and_ignores_content_type() -> None:
    client = FakeS3Client()
    client.objects[RESUME_KEY] = b"%PDF-1.7 bytes"
    store = S3ScheduleStore(client, "test-bucket")
    body, suffix = store.get_resume()
    assert body == b"%PDF-1.7 bytes"
    assert suffix == ".pdf"


def test_get_resume_alternate_key() -> None:
    client = FakeS3Client()
    client.objects["resume/quant-dev"] = b"%PDF-alt"
    store = S3ScheduleStore(client, "test-bucket")
    body, suffix = store.get_resume("resume/quant-dev")
    assert body == b"%PDF-alt"
    assert suffix == ".pdf"
    with pytest.raises(JDParserError) as ei:
        store.get_resume()
    assert ei.value.code == "SCHEDULE_RESUME_MISSING"


def test_resume_object_key_defaults_and_rejects_path_escape() -> None:
    assert resume_object_key(None) == RESUME_KEY
    assert resume_object_key("") == RESUME_KEY
    assert resume_object_key("quant-dev") == "resume/quant-dev"
    assert resume_object_key("resume/quant-dev") == "resume/quant-dev"
    with pytest.raises(JDParserError) as ei:
        resume_object_key("../secret")
    assert ei.value.code == "SCHEDULE_CONFIG_INVALID"


def test_default_config_when_missing(
    store_and_objs: tuple[ScheduleStore, dict[str, bytes]],
) -> None:
    store, _ = store_and_objs
    assert store.get_search_config() == ScheduleSearchConfig()


def test_invalid_config_json(
    store_and_objs: tuple[ScheduleStore, dict[str, bytes]],
) -> None:
    store, objs = store_and_objs
    objs[CONFIG_KEY] = b'{"broaden_search": "not-a-bool"}'
    with pytest.raises(JDParserError) as ei:
        store.get_search_config()
    assert ei.value.code == "SCHEDULE_CONFIG_INVALID"


def test_day_lock_create_only_412(
    store_and_objs: tuple[ScheduleStore, dict[str, bytes]],
) -> None:
    store, _ = store_and_objs
    lock = _day_lock()
    store.put_day_lock(lock, create_only=True)
    with pytest.raises(JDParserError) as ei:
        store.put_day_lock(lock.model_copy(update={"run_id": "run-2"}), create_only=True)
    assert ei.value.code == "SCHEDULE_LOCK_HELD"
    loaded = store.get_day_lock(lock.schedule_date)
    assert loaded is not None
    assert loaded.run_id == "run-1"


def test_day_lock_search_preset_is_separate_key(
    store_and_objs: tuple[ScheduleStore, dict[str, bytes]],
) -> None:
    store, objs = store_and_objs
    store.put_day_lock(_day_lock(run_id="run-default"), create_only=True)
    store.put_day_lock(_day_lock(run_id="run-texas"), create_only=True, search="texas")
    default = store.get_day_lock("2026-08-25")
    texas = store.get_day_lock("2026-08-25", search="texas")
    assert default is not None and default.run_id == "run-default"
    assert texas is not None and texas.run_id == "run-texas"
    assert "state/day/2026-08-25.json" in objs
    assert "state/day/2026-08-25/texas.json" in objs


def test_day_lock_search_slug_must_be_path_safe(
    store_and_objs: tuple[ScheduleStore, dict[str, bytes]],
) -> None:
    store, _ = store_and_objs
    with pytest.raises(JDParserError) as ei:
        store.put_day_lock(_day_lock(run_id="run-bad"), create_only=True, search="New York")
    assert ei.value.code == "SCHEDULE_CONFIG_INVALID"
    store.put_day_lock(_day_lock(run_id="run-paris"), create_only=True, search="paris")
    loaded = store.get_day_lock("2026-08-25", search="paris")
    assert loaded is not None
    assert loaded.run_id == "run-paris"


def test_notified_missing_is_empty(
    store_and_objs: tuple[ScheduleStore, dict[str, bytes]],
) -> None:
    store, _ = store_and_objs
    ns = store.get_notified()
    assert ns.jobs == {}
    assert ns.urls == {}
    assert ns.updated_at


def test_archive_key_shape(
    store_and_objs: tuple[ScheduleStore, dict[str, bytes]],
) -> None:
    store, objs = store_and_objs
    rec = _archived_run(schedule_date="2026-08-25", run_id="abc-123")
    key = store.put_archived_run(rec)
    assert key == "runs/2026-08-25/abc-123.json"
    assert key in objs


def test_hydrate_persist_round_trip(
    tmp_path: Path,
    store_and_objs: tuple[ScheduleStore, dict[str, bytes]],
) -> None:
    store, _ = store_and_objs
    rec = stored_profile(cache_key="ck")
    src = tmp_path / "src"
    src.mkdir()
    (src / f"{rec.cache_key}.json").write_text(rec.model_dump_json(), encoding="utf-8")
    assert store.persist_profiles(src) == 1
    dest = tmp_path / "dest"
    dest.mkdir()
    assert store.hydrate_profiles(dest) == 1
    loaded = (dest / f"{rec.cache_key}.json").read_text(encoding="utf-8")
    assert loaded == rec.model_dump_json()


def test_list_objects_v2_pagination(tmp_path: Path) -> None:
    client = FakeS3Client(page_size=1)
    store = S3ScheduleStore(client, "test-bucket")
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.json").write_text("{}", encoding="utf-8")
    (src / "b.json").write_text("{}", encoding="utf-8")
    assert store.persist_profiles(src) == 2
    dest = tmp_path / "dest"
    dest.mkdir()
    assert store.hydrate_profiles(dest) == 2
    assert (dest / "a.json").read_text(encoding="utf-8") == "{}"
    assert (dest / "b.json").read_text(encoding="utf-8") == "{}"
    tokens = [c.get("ContinuationToken") for c in client.list_calls]
    assert tokens[0] is None
    assert any(t is not None for t in tokens[1:])
    assert len(client.list_calls) >= 2


def test_s3_other_errors_map_to_schedule_s3() -> None:
    class _Boom:
        def get_object(self, **kwargs: Any) -> dict[str, Any]:
            raise _client_error("AccessDenied", 403, "GetObject")

    store = S3ScheduleStore(_Boom(), "test-bucket")
    with pytest.raises(JDParserError) as ei:
        store.get_resume()
    assert ei.value.code == "SCHEDULE_S3"


def test_s3_store_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeBoto3:
        def client(self, name: str) -> FakeS3Client:
            assert name == "s3"
            return FakeS3Client()

    monkeypatch.setenv("SCHEDULE_BUCKET", "my-bucket")
    monkeypatch.setitem(__import__("sys").modules, "boto3", _FakeBoto3())
    store = s3_store_from_env()
    assert store._bucket == "my-bucket"
