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
from jdparser.llm.client import _call, _reasoning_body, get_client
from jdparser.llm.schemas import (
    AdzunaQuery,
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
def _profile() -> ResumeProfile:
    return ResumeProfile(
        roles=["backend engineer"],
        skills=["python"],
        seniority="senior",
        total_years_experience=8.0,
        domains=["fintech"],
        work_authorization=["us_citizen"],
        locations=["Austin, TX"],
        remote_preference="remote",
        employment_types=["full_time"],
        evidence=[ResumeEvidence(claim="8 years", source_quote="8 years building payments")],
    )


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


ALL_INSTANCES: list[BaseModel] = [
    ResumeEvidence(claim="c", source_quote="q"),
    _profile(),
    Fingerprint(file_hash="a", text_hash="b", cache_key="c"),
    StoredResumeProfile(
        id="id-1",
        user_id="local",
        file_hash="a",
        text_hash="b",
        cache_key="c",
        profile=_profile(),
        parser_version="1.0.0",
        schema_version="1.0.0",
        model="z-ai/glm-5.2",
        created_at="2026-06-26T00:00:00Z",
        updated_at="2026-06-26T00:00:00Z",
    ),
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
            decision="qualified",
            confidence=1.5,
            met_requirements=[],
            missing_hard_requirements=[],
            failed_dealbreakers=[],
            rationale="x",
        )


def test_adzuna_query_defaults() -> None:
    """results_per_page / pages defaults come from config (SPEC §3.4/§6.1)."""
    q = AdzunaQuery(what="engineer")
    assert q.results_per_page == ADZUNA_DEFAULT_RESULTS_PER_PAGE
    assert q.pages == 2


def test_config_env_override_and_restore() -> None:
    """LLM_NODES['judge'] reflects env overrides, then restores to defaults so other
    tests / the live smoke see 10000 / 'low' (SPEC §6.3)."""
    os.environ["LLM_MAX_TOKENS_JUDGE"] = "123"
    os.environ["LLM_REASONING_JUDGE"] = "medium"
    try:
        importlib.reload(jdparser.config)
        assert jdparser.config.LLM_NODES["judge"]["max_tokens"] == 123
        assert jdparser.config.LLM_NODES["judge"]["reasoning"] == "medium"
    finally:
        os.environ.pop("LLM_MAX_TOKENS_JUDGE", None)
        os.environ.pop("LLM_REASONING_JUDGE", None)
        importlib.reload(jdparser.config)
    assert jdparser.config.LLM_NODES["judge"]["max_tokens"] == 10000
    assert jdparser.config.LLM_NODES["judge"]["reasoning"] == "low"


def test_reasoning_body() -> None:
    """reasoning 'off' disables; any other value sets effort (SPEC §6.3)."""
    assert _reasoning_body("off") == {"reasoning": {"enabled": False}}
    assert _reasoning_body("low") == {"reasoning": {"effort": "low"}}
    assert _reasoning_body("medium") == {"reasoning": {"effort": "medium"}}


class _Dummy(BaseModel):
    x: int


def test_truncation_guard_raises_llm_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful completion flagged finish_reason='length' surfaces LLM_TRUNCATED,
    NOT a *_INVALID (SPEC §6.4 / truncation guard)."""
    completions = get_client().chat.completions
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


def test_client_module_importable() -> None:
    """get_client constructs without a network call (constructing is fine)."""
    assert get_client() is client_mod.get_client()
