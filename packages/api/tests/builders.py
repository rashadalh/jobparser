"""Shared test builders — import as `from builders import profile, stored_profile, job`.

Before this file existed, `ResumeProfile(...)` was hand-built in six places across five
test files and `StoredResumeProfile(...)` in five (REFACTOR_AUDIT F3). ResumeProfile has 12
required fields and its schema has already been extended twice (SCHEMA_VERSION 1.2.0), so
that cost was being paid on every schema change.

This is a plain module rather than conftest.py: nothing here is a pytest fixture, and
conftest is for fixtures/hooks pytest injects, not a grab-bag importable by name.

They are functions rather than fixtures because most call sites want several profiles in
one test, or want to tweak one field, and a fixture would force a `request.getfixturevalue`
dance for no gain. Import them directly.

Each takes keyword overrides so a test can vary the ONE field it cares about while the
other eleven stay boring and out of the way.
"""

from typing import Any

from jdparser.jobs import Job
from jdparser.llm.schemas import (
    ResumeEvidence,
    ResumeProfile,
    StoredResumeProfile,
)


def profile(**overrides: Any) -> ResumeProfile:
    """A senior backend engineer. Override any field by keyword."""
    base = ResumeProfile(
        roles=["backend engineer"],
        skills=["python"],
        seniority="senior",
        total_years_experience=8.0,
        work_periods=[],
        education=["B.S. Computer Science"],
        domains=["fintech"],
        work_authorization=["us_citizen"],
        locations=["Austin, TX"],
        remote_preference="remote",
        employment_types=["full_time"],
        evidence=[ResumeEvidence(claim="8 years", source_quote="8 years building payments")],
    )
    return base.model_copy(update=overrides) if overrides else base


def stored_profile(**overrides: Any) -> StoredResumeProfile:
    """A cache record wrapping `profile()`. Override any field by keyword.

    `parser_version` / `schema_version` are pinned to literals rather than the live
    constants: these records stand in for what is ALREADY on disk, and a test asserting
    cache behavior should not silently change meaning when a version constant is bumped.
    """
    base = StoredResumeProfile(
        id="id-1",
        user_id="local",
        cache_key="ck",
        profile=profile(),
        parser_version="1.0.0",
        schema_version="1.0.0",
        model="google/gemini-3.1-flash-lite",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    return base.model_copy(update=overrides) if overrides else base


def job(**overrides: Any) -> Job:
    """A normalized posting, as a source client would emit it."""
    base = Job(
        id="job-0",
        title="Engineer",
        company="Acme Inc",
        location="Austin, TX",
        description="snippet",
        redirect_url="https://adzuna.example/land/0",
        raw={},
    )
    return base.model_copy(update=overrides) if overrides else base
