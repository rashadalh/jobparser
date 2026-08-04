"""Schema + config-override + reasoning-body + truncation-guard tests (SPEC §3, §6.3,
§6.4 / IMPLEMENTATION_LLM). All mocked — ZERO network / live LLM calls."""

import importlib
import os
import types

import pytest
from pydantic import BaseModel, ValidationError

import jdparser.config
from jdparser.config import (
    ADZUNA_DEFAULT_RESULTS_PER_PAGE,
    JDParserError,
    NodeCfg,
)
from jdparser.llm import client as client_mod
from builders import profile as _profile, stored_profile
from jdparser.llm.client import _call, _reasoning_body
from jdparser.llm.schemas import (
    AdzunaQuery,
    CandidateNote,
    CandidateNotes,
    ErrorRecord,
    EvaluatedJob,
    Fingerprint,
    FitJudgment,
    JobRequirements,
    MetRequirement,
    QualityResult,
    FetchResult,
    ResumeEvidence,
    ResumeProfile,
    RunRecord,
    SearchPlan,
    StoredResumeProfile,
)


# --- minimal valid instances of every model ----------------------------------
def _requirements() -> JobRequirements:
    return JobRequirements(
        required_skills=["python"],
        preferred_skills=["go"],
        min_years_experience=5.0,
        education=["BS Computer Science"],
        education_required=False,
        location_constraints=["Austin, TX"],
        remote_allowed=True,
        responsibilities=["build services"],
        dealbreakers=["active TS/SCI clearance"],
        employment_type="full_time",
    )


def _judgment() -> FitJudgment:
    return FitJudgment(
        thematic_fit=True,
        relevant_years_experience=5.0,
        thematic_rationale="backend roles match the JD's function",
        decision="qualified",
        confidence=0.9,
        met_requirements=[MetRequirement(requirement="python", evidence_quote="python expert")],
        missing_hard_requirements=[],
        failed_dealbreakers=[],
        rationale="strong match",
    )


def _evaluated_job() -> EvaluatedJob:
    return EvaluatedJob(
        job_id="123",
        title="Senior Backend Engineer",
        company="Acme Inc",
        location="Austin, TX",
        final_url="https://jobs.example.com/123",
        source={"id": "123", "title": "Senior Backend Engineer"},
        jd_char_len=1200,
        requirements=_requirements(),
        judgment=_judgment(),
        status="qualified",
        failure_stage=None,
    )


def _run_record() -> RunRecord:
    return RunRecord(
        run_id="run-1",
        user_id="local",
        status="completed",
        created_at="2026-06-26T00:00:00Z",
        updated_at="2026-06-26T00:01:00Z",
        resume_cache_hit=True,
        qualified_jobs=[{"job_id": "123"}],
        failures=[],
        rejected=[],
        errors=[{"code": "FETCH_FAILED"}],
        error=None,
    )


def _candidate_note() -> CandidateNote:
    return CandidateNote(note="No active clearance", kind="dealbreaker", source="feedback on 'Acme SWE'")


ALL_INSTANCES: list[BaseModel] = [
    ResumeEvidence(claim="c", source_quote="q"),
    _profile(),
    _candidate_note(),
    CandidateNotes(notes=[_candidate_note()]),
    Fingerprint(cache_key="c"),
    stored_profile(),
    AdzunaQuery(what="backend engineer"),
    SearchPlan(queries=[AdzunaQuery(what="backend engineer")]),
    _requirements(),
    MetRequirement(requirement="python", evidence_quote="python expert"),
    _judgment(),
    FetchResult(url="https://x", status=200, html="<html></html>", source="http"),
    QualityResult(char_len=1200, passed=True, reasons=[]),
    _evaluated_job(),
    ErrorRecord(job_id=None, stage="adzuna_search", code="ADZUNA_HTTP", message="boom"),
    _run_record(),
]


@pytest.mark.parametrize("inst", ALL_INSTANCES, ids=lambda m: type(m).__name__)
def test_model_round_trips(inst: BaseModel) -> None:
    """Every model round-trips model_validate(model_dump())."""
    restored = type(inst).model_validate(inst.model_dump())
    assert restored == inst


def test_adzuna_query_rejects_unknown_field() -> None:
    """extra='forbid' — the planner cannot smuggle params (SPEC §3.4). Use
    model_validate so an intentionally-unknown field stays type-clean (the
    constructor's typed kwargs would otherwise fail mypy, not Pydantic)."""
    with pytest.raises(ValidationError):
        AdzunaQuery.model_validate({"what": "engineer", "foo": 1})


def test_fit_judgment_confidence_bounds() -> None:
    """confidence must be within [0.0, 1.0] (SPEC §3.6)."""
    with pytest.raises(ValidationError):
        FitJudgment(
            thematic_fit=True,
            relevant_years_experience=5.0,
            thematic_rationale="x",
            decision="qualified",
            confidence=1.5,
            met_requirements=[],
            missing_hard_requirements=[],
            failed_dealbreakers=[],
            rationale="x",
        )


def test_required_skills_rejects_embedded_duration_clause() -> None:
    """A JD bullet like '5+ years of work experience in X' must not be smuggled into
    `required_skills` as a single literal string — that duration half can never be
    proven by a verbatim resume quote (see fit_judge.py MetRequirement.evidence_quote)."""
    bad = _requirements().model_dump() | {
        "required_skills": [
            "5+ years of work experience in developing FO pricing models or market risk models"
        ]
    }
    with pytest.raises(ValidationError):
        JobRequirements.model_validate(bad)


def test_required_skills_accepts_clean_skill_only_entry() -> None:
    """No false rejection: a skill/domain description with no duration clause is fine."""
    ok = _requirements().model_dump() | {
        "required_skills": ["FO pricing models or market risk models development"]
    }
    reqs = JobRequirements.model_validate(ok)
    assert reqs.required_skills == ["FO pricing models or market risk models development"]


@pytest.mark.parametrize(
    "skill",
    [
        "Trading experience with 2-year and 10-year Treasury futures",
        "Knowledge of the 10-year Treasury yield curve",
    ],
)
def test_required_skills_does_not_false_positive_on_instrument_tenor(skill: str) -> None:
    """The whole reason the regex requires 'years' to be followed by 'experience':
    quant-finance tenor language ('10-year Treasury', '2-year note') is a legitimate
    required skill, not a duration-of-experience claim, and must not be rejected."""
    reqs = JobRequirements.model_validate(_requirements().model_dump() | {"required_skills": [skill]})
    assert reqs.required_skills == [skill]


def test_stored_resume_profile_notes_default_and_old_shape_still_validates() -> None:
    """`notes` defaults to [] so an old-shaped cached profile JSON (written before this
    field existed) still validates without a SCHEMA_VERSION bump / cache invalidation."""
    old_shaped = stored_profile()
    assert old_shaped.notes == []

    old_json = old_shaped.model_dump()
    del old_json["notes"]  # simulate a file written before the field existed
    assert StoredResumeProfile.model_validate(old_json).notes == []


def test_run_record_resume_cache_key_defaults_none() -> None:
    rec = _run_record()
    assert rec.resume_cache_key is None
    assert RunRecord.model_validate(rec.model_dump() | {"resume_cache_key": "ck-1"}).resume_cache_key == "ck-1"


def test_adzuna_query_defaults() -> None:
    """results_per_page / pages defaults come from config (SPEC §3.4/§6.1)."""
    q = AdzunaQuery(what="engineer")
    assert q.results_per_page == ADZUNA_DEFAULT_RESULTS_PER_PAGE
    assert q.pages == 2


def test_config_env_override_and_restore() -> None:
    """LLM_NODES['judge'] reflects env overrides, then restores to defaults so other
    tests / the live smoke see 10000 / 'medium' (SPEC §6.3)."""
    os.environ["LLM_MAX_TOKENS_JUDGE"] = "123"
    os.environ["LLM_REASONING_JUDGE"] = "high"
    try:
        importlib.reload(jdparser.config)
        assert jdparser.config.LLM_NODES["judge"]["max_tokens"] == 123
        assert jdparser.config.LLM_NODES["judge"]["reasoning"] == "high"
    finally:
        os.environ.pop("LLM_MAX_TOKENS_JUDGE", None)
        os.environ.pop("LLM_REASONING_JUDGE", None)
        importlib.reload(jdparser.config)
    assert jdparser.config.LLM_NODES["judge"]["max_tokens"] == 10000
    assert jdparser.config.LLM_NODES["judge"]["reasoning"] == "medium"


def test_reasoning_body() -> None:
    """reasoning 'off' disables; any other value sets effort (SPEC §6.3)."""
    assert _reasoning_body("off")["reasoning"] == {"enabled": False}
    assert _reasoning_body("low")["reasoning"] == {"effort": "low"}
    assert _reasoning_body("medium")["reasoning"] == {"effort": "medium"}


def test_every_call_asks_openrouter_for_cost() -> None:
    """Without `usage.include` OpenRouter returns tokens but no cost, and the run's
    spend silently becomes unknowable — every reasoning setting must carry it."""
    for setting in ("off", "low", "medium", "high"):
        assert _reasoning_body(setting)["usage"] == {"include": True}


class _Dummy(BaseModel):
    x: int


def test_truncation_guard_raises_llm_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful completion flagged finish_reason='length' surfaces LLM_TRUNCATED,
    NOT a *_INVALID (SPEC §6.4 / truncation guard)."""
    completions = client_mod._client.chat.completions
    fake_completion = types.SimpleNamespace(
        choices=[types.SimpleNamespace(finish_reason="length")]
    )

    def fake_create_with_completion(*args: object, **kwargs: object) -> tuple[_Dummy, object]:
        return _Dummy(x=1), fake_completion

    monkeypatch.setattr(completions, "create_with_completion", fake_create_with_completion)

    cfg: NodeCfg = {"model": "test", "temperature": 0.0, "max_tokens": 10, "reasoning": "off"}
    with pytest.raises(JDParserError) as ei:
        _call(cfg, "system", "user", _Dummy, "PROFILE_INVALID")
    assert ei.value.code == "LLM_TRUNCATED"
