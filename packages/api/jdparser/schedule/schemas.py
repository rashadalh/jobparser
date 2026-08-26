"""Schedule-plane Pydantic models — specs/auto-job-recommendations/SPEC.md §3."""

from typing import Any, Literal

from pydantic import BaseModel

DayLockStatus = Literal["running", "completed", "failed"]
ScheduleStatus = Literal["completed", "skipped", "failed"]


class ScheduleSearchConfig(BaseModel):
    locations: list[str] | None = None   # None = profile inferred locations
    broaden_search: bool = True
    max_days_old: int | None = 7         # days; None or <=0 = any age
    include_agencies: bool = False


class DayLock(BaseModel):
    schedule_date: str          # SPEC §3.1
    status: DayLockStatus
    run_id: str
    started_at: str             # ISO-8601 UTC
    updated_at: str             # ISO-8601 UTC
    run_s3_key: str | None = None
    error: str | None = None
    new_notified: int | None = None  # count; set on completed


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


class ScheduleResult(BaseModel):
    ok: bool
    status: ScheduleStatus
    schedule_date: str
    run_id: str | None = None
    run_s3_key: str | None = None
    new_notified: int = 0
    skip_reason: str | None = None


class ArchivedRun(BaseModel):
    schedule_date: str
    search_config: ScheduleSearchConfig
    # reason: RunRecord.model_dump(); extra="forbid" is NOT applied to this dict (SPEC §3.7)
    run: dict[str, Any]
