"""Pydantic models — SPEC §3 (+ §5.2). Single owner of every model in the system.

The resume-profile / fingerprint / cache records (SPEC §3.3, §3.7, §3.8.4) are
field-identical to Phase 1 (the cache + its tests depend on them); the remaining
LLM/boundary/run models (SPEC §3.4–§3.6, §3.8.2–§3.10, §5.2) are appended below.

All on-disk and on-the-wire JSON is snake_case (SPEC §10, item 1).
"""

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    cache_key: str


# --- Candidate notes (feedback distillation) ---------------------------------
class CandidateNote(BaseModel):
    note: str
    kind: Literal["dealbreaker", "preference", "context"]
    source: str   # e.g. "feedback on 'Senior SWE @ Acme'"


class CandidateNotes(BaseModel):
    """instructor wrapper: response_model must be a single schema (IMPLEMENTATION_LLM)."""

    notes: list[CandidateNote]


# --- §3.7 StoredResumeProfile (durable cache record) -------------------------
class StoredResumeProfile(BaseModel):
    id: str
    user_id: str
    cache_key: str
    profile: ResumeProfile
    parser_version: str
    schema_version: str
    model: str
    created_at: str
    updated_at: str
    notes: list[CandidateNote] = []  # candidate-corrected feedback (no SCHEMA_VERSION bump — default-safe)


# --- §3.4 AdzunaQuery (logic — gemini-3.1-flash-lite) — closed schema ----------------------
class AdzunaQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")  # planner MUST NOT invent params

    what: str
    what_or: str | None = None     # OR keywords (any term) — Adzuna ANDs this group with `what`
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
    """Relevance pre-screen output. ``relevant_job_ids`` are the in-field jobs ranked
    most-relevant-first (the caller caps to the top N). ``agency_job_ids`` is the subset
    of those that look like third-party recruitment/staffing-agency postings — a SECOND
    relevance dimension: deprioritized (and screened out) unless the user includes them."""

    relevant_job_ids: list[str]
    agency_job_ids: list[str] = []


# --- §3.5 JobRequirements (text extraction — Gemini 3.1 Flash Lite) ----------
# Anchored on "years ... experience", NOT bare "\d+\s*years?" — this domain is
# quant-finance, where "10-year Treasury" / "2-year note" / "5-year CDS" are
# legitimate required skills, not duration-of-experience claims. Requiring
# "experience" adjacent targets only the actual bug pattern ("N+ years of work
# experience in X") and leaves tenor language alone.
_YEARS_EXPERIENCE_CLAUSE = re.compile(
    r"\b\d+\+?\s*years?\s+(?:of\s+)?(?:work\s+)?experience\b", re.IGNORECASE
)


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

    @field_validator("required_skills")
    @classmethod
    def _no_embedded_duration_clause(cls, skills: list[str]) -> list[str]:
        for s in skills:
            if _YEARS_EXPERIENCE_CLAUSE.search(s):
                raise ValueError(
                    f"{s!r} embeds a years-of-experience duration clause — move the "
                    "number into min_years_experience and keep only the skill/domain "
                    "description here (no digit + 'year(s) ... experience' phrase)."
                )
        return skills


# --- §3.6 FitJudgment (logic — gemini-3.1-flash-lite) --------------------------------------
FitDecision = Literal["qualified", "not_qualified", "uncertain"]


class MetRequirement(BaseModel):
    requirement: str
    evidence_quote: str


class FitJudgment(BaseModel):
    # Forced-sequencing fields (declared before `decision` so structured-output field
    # order pushes the model to commit to these BEFORE it computes the final decision —
    # otherwise it tends to reach `decision` via raw skill/years matching and rationalize
    # thematic fit as an afterthought):
    thematic_fit: bool               # same profession/specialization as the candidate's target roles/domains?
    relevant_years_experience: float # years from work_periods actually in THIS JD's specialization —
    #                                  derived by the model from titles/orgs/skills, NOT profile.total_years_experience
    #                                  (that figure is a domain-blind career-wide total; see fit_judge.py THEMATIC FIT)
    thematic_rationale: str          # which work_periods/skills were counted and why the specialization does/doesn't match
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
    # Set at the relevance screen (company + snippet); rides through for the UI "Agency"
    # badge. Only ever true on included-agency runs (else agencies are screened out).
    is_recruitment_agency: bool = False


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
    resume_cache_key: str | None = None   # candidate identity (cache_key) this run belongs to
    # live progress (written mid-run by the streaming executor; None until first update).
    phase: str | None = None              # human label for the current pipeline stage
    jobs_total: int | None = None         # jobs entering the eval fan-out (known after screen)
    jobs_done: int | None = None          # jobs evaluated so far (drives the progress bar)
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
