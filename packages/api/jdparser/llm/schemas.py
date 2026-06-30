"""Pydantic models — SPEC §3 (+ §5.2). Single owner of every model in the system.

The resume-profile / fingerprint / cache records (SPEC §3.3, §3.7, §3.8.4) are
field-identical to Phase 1 (the cache + its tests depend on them); the remaining
LLM/boundary/run models (SPEC §3.4–§3.6, §3.8.2–§3.10, §5.2) are appended below.

All on-disk and on-the-wire JSON is snake_case (SPEC §10, item 1).
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from jdparser.config import (
    ADZUNA_DEFAULT_RESULTS_PER_PAGE,
    ADZUNA_MAX_PAGES,
    ADZUNA_MAX_RESULTS_PER_PAGE,
)

# --- §3.3 ResumeProfile (logic — gemini-3.1-flash-lite) ------------------------------------
Seniority = Literal["intern", "junior", "mid", "senior", "staff", "principal", "executive"]
RemotePref = Literal["onsite", "hybrid", "remote", "any"]
EmploymentType = Literal["full_time", "part_time", "contract", "permanent"]


class ResumeEvidence(BaseModel):
    claim: str
    source_quote: str


class WorkPeriod(BaseModel):
    """One dated professional role. Dates are DECIMAL YEARS (Jan 2018 -> 2018.0,
    Jul 2020 -> 2020.5, "2019" -> 2019.0); ``end_year=None`` means ongoing ("Present").
    `total_years_experience` is computed deterministically from these (interval union)."""

    title: str
    organization: str
    start_year: float
    end_year: float | None = None


class ResumeProfile(BaseModel):
    roles: list[str]
    skills: list[str]
    seniority: Seniority
    total_years_experience: float          # COMPUTED in code from `work_periods` (interval union)
    work_periods: list[WorkPeriod]         # dated roles the years total is computed from
    education: list[str]                    # degrees/credentials, e.g. ["M.S. Computer Science", "B.S. ..."]
    domains: list[str]
    work_authorization: list[str]
    locations: list[str]
    remote_preference: RemotePref
    employment_types: list[EmploymentType]
    evidence: list[ResumeEvidence]


# --- §3.8.4 Fingerprint ------------------------------------------------------
class Fingerprint(BaseModel):
    file_hash: str
    text_hash: str
    cache_key: str


# --- §3.7 StoredResumeProfile (durable cache record) -------------------------
class StoredResumeProfile(BaseModel):
    id: str
    user_id: str
    file_hash: str
    text_hash: str
    cache_key: str
    profile: ResumeProfile
    parser_version: str
    schema_version: str
    model: str
    created_at: str
    updated_at: str


# --- §3.4 AdzunaQuery (logic — gemini-3.1-flash-lite) — closed schema ----------------------
class AdzunaQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")  # planner MUST NOT invent params

    what: str
    what_exclude: str | None = None
    where: str | None = None
    distance: int | None = None
    max_days_old: int | None = None
    category: str | None = None
    salary_min: int | None = None
    full_time: bool | None = None
    part_time: bool | None = None
    contract: bool | None = None
    permanent: bool | None = None
    results_per_page: int = Field(
        default=ADZUNA_DEFAULT_RESULTS_PER_PAGE, ge=1, le=ADZUNA_MAX_RESULTS_PER_PAGE
    )
    pages: int = Field(default=2, ge=1, le=ADZUNA_MAX_PAGES)


class SearchPlan(BaseModel):
    """instructor wrapper: response_model must be a single schema (IMPLEMENTATION_LLM)."""

    queries: list[AdzunaQuery]


class JobScreen(BaseModel):
    """Relevance pre-screen output: the job_ids plausibly in the candidate's field."""

    relevant_job_ids: list[str]


# --- §3.5 JobRequirements (text extraction — Gemini 3.1 Flash Lite) ----------
class JobRequirements(BaseModel):
    required_skills: list[str]
    preferred_skills: list[str]
    min_years_experience: float | None
    education: list[str]
    education_required: bool
    location_constraints: list[str]
    remote_allowed: bool | None
    responsibilities: list[str]
    dealbreakers: list[str]
    employment_type: EmploymentType | None


# --- §3.6 FitJudgment (logic — gemini-3.1-flash-lite) --------------------------------------
FitDecision = Literal["qualified", "not_qualified", "uncertain"]


class MetRequirement(BaseModel):
    requirement: str
    evidence_quote: str


class FitJudgment(BaseModel):
    decision: FitDecision
    confidence: float = Field(ge=0.0, le=1.0)
    met_requirements: list[MetRequirement]
    missing_hard_requirements: list[str]
    failed_dealbreakers: list[str]
    rationale: str


# --- §3.8.2 FetchResult ------------------------------------------------------
class FetchResult(BaseModel):
    url: str
    status: int
    html: str
    source: Literal["http", "playwright"]


# --- §3.8.3 QualityResult ----------------------------------------------------
class QualityResult(BaseModel):
    char_len: int
    passed: bool
    reasons: list[str]


# --- §3.9 EvaluatedJob -------------------------------------------------------
JobStatus = Literal["qualified", "not_qualified", "uncertain", "failed"]
FailureStage = Literal["resolve", "fetch", "extract", "quality", "parse", "judge"]


class EvaluatedJob(BaseModel):
    job_id: str
    title: str
    company: str
    location: str
    final_url: str | None
    source: dict[str, Any]  # reason: heterogeneous JSON passthrough (SPEC §3.8.1/§5.2)
    jd_char_len: int | None
    requirements: JobRequirements | None
    judgment: FitJudgment | None
    status: JobStatus
    failure_stage: FailureStage | None


# --- §3.10 ErrorRecord -------------------------------------------------------
ErrorStage = Literal[
    "resume_extract", "profile", "search_plan", "adzuna_search", "screen",
    "resolve", "fetch", "extract", "quality", "parse", "judge", "aggregate",
]


class ErrorRecord(BaseModel):
    job_id: str | None
    stage: ErrorStage
    code: str
    message: str
    # reason: heterogeneous JSON passthrough (SPEC §3.8.1/§5.2)
    detail: dict[str, Any] | None = None


# --- §5.2 RunRecord (API response + on-disk run record) ----------------------
RunStatus = Literal["pending", "running", "completed", "failed"]


class RunRecord(BaseModel):
    run_id: str
    user_id: str
    status: RunStatus
    created_at: str
    updated_at: str
    resume_cache_hit: bool | None
    # reason: heterogeneous JSON passthrough (ResumeProfile.model_dump(); §5.2)
    resume_profile: dict[str, Any] | None = None  # what the profiler extracted (audit/transparency)
    # reason: heterogeneous JSON passthrough (SPEC §3.8.1/§5.2)
    qualified_jobs: list[dict[str, Any]]
    # reason: heterogeneous JSON passthrough (SPEC §3.8.1/§5.2)
    failures: list[dict[str, Any]]
    # reason: heterogeneous JSON passthrough (SPEC §3.8.1/§5.2)
    rejected: list[dict[str, Any]]
    # reason: heterogeneous JSON passthrough (SPEC §3.8.1/§5.2)
    errors: list[dict[str, Any]]
    # jobs dropped by the relevance pre-screen (off-field) — audit/transparency
    screened_out: list[dict[str, Any]] = []  # reason: heterogeneous JSON passthrough
    error: str | None
