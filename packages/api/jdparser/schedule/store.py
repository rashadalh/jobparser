"""S3 archive I/O for the daily schedule plane — SPEC §3.2–§3.8, §4.2."""

import os
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from jdparser.config import JDParserError, now_iso
from jdparser.schedule.schemas import (
    ArchivedRun,
    DayLock,
    NotifiedSet,
    ScheduleSearchConfig,
)

RESUME_KEY = "resume/current"
CONFIG_KEY = "config/search.json"
NOTIFIED_KEY = "state/notified.json"
PROFILES_PREFIX = "profiles/"

_MISSING_CODES = frozenset({"NoSuchKey", "404", "NotFound"})
_PRECONDITION_CODES = frozenset({"PreconditionFailed", "412"})


class ScheduleStore(Protocol):
    def get_resume(self) -> tuple[bytes, str]: ...
    def get_search_config(self) -> ScheduleSearchConfig: ...
    def hydrate_profiles(self, dest_dir: Path) -> int: ...
    def persist_profiles(self, src_dir: Path) -> int: ...
    def put_archived_run(self, rec: ArchivedRun) -> str: ...
    def get_day_lock(self, schedule_date: str) -> DayLock | None: ...
    def put_day_lock(self, lock: DayLock, *, create_only: bool) -> None: ...
    def get_notified(self) -> NotifiedSet: ...
    def put_notified(self, ns: NotifiedSet) -> None: ...


def parse_resume_suffix(body: bytes) -> str:
    """SPEC §3.6: b'%PDF' prefix → '.pdf'; ZIP/PK with b'word/' in first 4096 → '.docx'; else '.txt'."""
    if body.startswith(b"%PDF"):
        return ".pdf"
    if body.startswith(b"PK") and b"word/" in body[:4096]:
        return ".docx"
    return ".txt"


def _validate_schedule_date(schedule_date: str) -> str:
    try:
        date.fromisoformat(schedule_date)
    except ValueError as exc:
        raise JDParserError(
            code="SCHEDULE_S3",
            message=f"invalid schedule_date: {schedule_date}",
        ) from exc
    return schedule_date


def _day_lock_key(schedule_date: str) -> str:
    return f"state/day/{_validate_schedule_date(schedule_date)}.json"


def _run_key(schedule_date: str, run_id: str) -> str:
    return f"runs/{_validate_schedule_date(schedule_date)}/{run_id}.json"


def _client_error_code_and_status(exc: BaseException) -> tuple[str, int | None]:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return "", None
    error = response.get("Error")
    code = ""
    if isinstance(error, dict) and error.get("Code") is not None:
        code = str(error["Code"])
    status: int | None = None
    meta = response.get("ResponseMetadata")
    if isinstance(meta, dict):
        raw_status = meta.get("HTTPStatusCode")
        if isinstance(raw_status, int):
            status = raw_status
        elif isinstance(raw_status, str) and raw_status.isdigit():
            status = int(raw_status)
    return code, status


def _is_missing(exc: BaseException) -> bool:
    code, status = _client_error_code_and_status(exc)
    return code in _MISSING_CODES or status == 404


def _is_precondition_failed(exc: BaseException) -> bool:
    code, status = _client_error_code_and_status(exc)
    return code in _PRECONDITION_CODES or status == 412


def _read_body(resp: Any) -> bytes:
    if not isinstance(resp, dict):
        raise JDParserError(code="SCHEDULE_S3", message="unexpected get_object response")
    body = resp.get("Body")
    if body is None:
        raise JDParserError(code="SCHEDULE_S3", message="get_object missing Body")
    read = getattr(body, "read", None)
    raw: Any = read() if callable(read) else body
    if isinstance(raw, bytes):
        return raw
    if isinstance(raw, str):
        return raw.encode("utf-8")
    raise JDParserError(code="SCHEDULE_S3", message="unexpected get_object body type")


def _s3_error(exc: BaseException) -> JDParserError:
    return JDParserError(code="SCHEDULE_S3", message=str(exc))


class S3ScheduleStore:
    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def _get_bytes(self, key: str) -> bytes | None:
        try:
            resp = self._client.get_object(Bucket=self._bucket, Key=key)
        except Exception as exc:
            if _is_missing(exc):
                return None
            raise _s3_error(exc) from exc
        try:
            return _read_body(resp)
        except JDParserError:
            raise
        except Exception as exc:
            raise _s3_error(exc) from exc

    def _put_bytes(
        self,
        key: str,
        body: bytes,
        *,
        if_none_match: bool,
        lock_on_412: bool,
    ) -> None:
        kwargs: dict[str, Any] = {
            "Bucket": self._bucket,
            "Key": key,
            "Body": body,
        }
        if if_none_match:
            kwargs["IfNoneMatch"] = "*"
        try:
            self._client.put_object(**kwargs)
        except Exception as exc:
            if lock_on_412 and _is_precondition_failed(exc):
                raise JDParserError(code="SCHEDULE_LOCK_HELD") from exc
            raise _s3_error(exc) from exc

    def get_resume(self) -> tuple[bytes, str]:
        body = self._get_bytes(RESUME_KEY)
        if body is None:
            raise JDParserError(code="SCHEDULE_RESUME_MISSING")
        return body, parse_resume_suffix(body)

    def get_search_config(self) -> ScheduleSearchConfig:
        body = self._get_bytes(CONFIG_KEY)
        if body is None:
            return ScheduleSearchConfig()
        try:
            return ScheduleSearchConfig.model_validate_json(body)
        except ValidationError as exc:
            raise JDParserError(code="SCHEDULE_CONFIG_INVALID", message=str(exc)) from exc

    def hydrate_profiles(self, dest_dir: Path) -> int:
        dest_dir.mkdir(parents=True, exist_ok=True)
        token: str | None = None
        count = 0
        while True:
            kwargs: dict[str, Any] = {
                "Bucket": self._bucket,
                "Prefix": PROFILES_PREFIX,
            }
            if token is not None:
                kwargs["ContinuationToken"] = token
            try:
                resp = self._client.list_objects_v2(**kwargs)
            except Exception as exc:
                raise _s3_error(exc) from exc
            contents = resp.get("Contents") or []
            for obj in contents:
                key = str(obj.get("Key", ""))
                rel = key[len(PROFILES_PREFIX) :] if key.startswith(PROFILES_PREFIX) else key
                if not rel or rel.endswith("/"):
                    continue
                data = self._get_bytes(key)
                if data is None:
                    raise JDParserError(
                        code="SCHEDULE_S3",
                        message=f"listed profile missing: {key}",
                    )
                out = dest_dir / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(data)
                count += 1
            if not resp.get("IsTruncated"):
                break
            nxt = resp.get("NextContinuationToken")
            if not nxt:
                break
            token = str(nxt)
        return count

    def persist_profiles(self, src_dir: Path) -> int:
        count = 0
        for path in src_dir.glob("*.json"):
            self._put_bytes(
                f"{PROFILES_PREFIX}{path.name}",
                path.read_bytes(),
                if_none_match=False,
                lock_on_412=False,
            )
            count += 1
        return count

    def put_archived_run(self, rec: ArchivedRun) -> str:
        run_id = str(rec.run["run_id"])
        key = _run_key(rec.schedule_date, run_id)
        self._put_bytes(
            key,
            rec.model_dump_json().encode("utf-8"),
            if_none_match=True,
            lock_on_412=False,
        )
        return key

    def get_day_lock(self, schedule_date: str) -> DayLock | None:
        body = self._get_bytes(_day_lock_key(schedule_date))
        if body is None:
            return None
        try:
            return DayLock.model_validate_json(body)
        except ValidationError as exc:
            raise JDParserError(code="SCHEDULE_S3", message=str(exc)) from exc

    def put_day_lock(self, lock: DayLock, *, create_only: bool) -> None:
        self._put_bytes(
            _day_lock_key(lock.schedule_date),
            lock.model_dump_json().encode("utf-8"),
            if_none_match=create_only,
            lock_on_412=create_only,
        )

    def get_notified(self) -> NotifiedSet:
        body = self._get_bytes(NOTIFIED_KEY)
        if body is None:
            return NotifiedSet(jobs={}, urls={}, updated_at=now_iso())
        try:
            return NotifiedSet.model_validate_json(body)
        except ValidationError as exc:
            raise JDParserError(code="SCHEDULE_S3", message=str(exc)) from exc

    def put_notified(self, ns: NotifiedSet) -> None:
        self._put_bytes(
            NOTIFIED_KEY,
            ns.model_dump_json().encode("utf-8"),
            if_none_match=False,
            lock_on_412=False,
        )


def s3_store_from_env() -> S3ScheduleStore:
    import boto3  # lazy

    bucket = os.environ["SCHEDULE_BUCKET"]
    return S3ScheduleStore(boto3.client("s3"), bucket)
