"""Pydantic models — SPEC §3. Phase 1 defines only the resume-profile, cache, and
fingerprint records; later phases append the remaining LLM/boundary models.

All on-disk and on-the-wire JSON is snake_case (SPEC §10, item 1).
"""

from typing import Literal

from pydantic import BaseModel

Seniority = Literal["intern", "junior", "mid", "senior", "staff", "principal", "executive"]
RemotePref = Literal["onsite", "hybrid", "remote", "any"]
EmploymentType = Literal["full_time", "part_time", "contract", "permanent"]


class ResumeEvidence(BaseModel):
    claim: str
    source_quote: str


class ResumeProfile(BaseModel):
    roles: list[str]
    skills: list[str]
    seniority: Seniority
    total_years_experience: float
    domains: list[str]
    work_authorization: list[str]
    locations: list[str]
    remote_preference: RemotePref
    employment_types: list[EmploymentType]
    evidence: list[ResumeEvidence]


class Fingerprint(BaseModel):
    file_hash: str
    text_hash: str
    cache_key: str


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


# Phase 2 appends the remaining LLM/boundary models below.
