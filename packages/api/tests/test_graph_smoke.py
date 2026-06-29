"""Phase 5 graph smoke tests — SPEC §7.1–§7.6, §3.9, §3.10 (BUILD.md M5 acceptance).

ZERO live calls: every Adzuna / extract / LLM function is monkeypatched in the
namespace where it is **used** (``jdparser.graph.nodes.*`` for top-level nodes,
``jdparser.graph.subgraph.*`` for per-job stages). The real ``extract_text`` /
``compute_fingerprint`` run against the committed ``sample_resume.pdf`` fixture (no
network); ``dedupe`` is pure. Integration scenarios build the REAL graph via
``build_graph()`` and ``.invoke(...)``.
"""

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from jdparser.config import (
    CONFIDENCE_THRESHOLD,
    EVAL_FANOUT_CONCURRENCY,
    MIN_JD_CHARS,
    JDParserError,
)
from jdparser.graph.build import build_graph
from jdparser.graph.nodes import aggregate_matches, is_qualified
from jdparser.graph.state import JobMatchState
from jdparser.graph.subgraph import _evaluated_job
from jdparser.llm.schemas import (
    AdzunaQuery,
    FetchResult,
    FitJudgment,
    JobRequirements,
    MetRequirement,
    QualityResult,
    ResumeEvidence,
    ResumeProfile,
    StoredResumeProfile,
)

RESUME = str(Path(__file__).parent / "fixtures" / "sample_resume.pdf")


# --- fixtures / builders -----------------------------------------------------
def _profile() -> ResumeProfile:
    return ResumeProfile(
        roles=["Backend Engineer"],
        skills=["python", "distributed systems"],
        seniority="senior",
        total_years_experience=6.0,
        domains=["fintech"],
        work_authorization=["us_citizen"],
        locations=["Austin, TX"],
        remote_preference="remote",
        employment_types=["full_time"],
        evidence=[ResumeEvidence(claim="6 years backend", source_quote="6 years building backends")],
    )


def _stored_profile() -> StoredResumeProfile:
    return StoredResumeProfile(
        id="seed-id",
        user_id="local",
        file_hash="fh",
        text_hash="th",
        cache_key="ck",
        profile=_profile(),
        parser_version="1.0.0",
        schema_version="1.0.0",
        model="z-ai/glm-5.2",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


def _req() -> JobRequirements:
    return JobRequirements(
        required_skills=["python"],
        preferred_skills=[],
        min_years_experience=None,
        education=[],
        education_required=False,
        location_constraints=[],
        remote_allowed=None,
        responsibilities=[],
        dealbreakers=[],
        employment_type=None,
    )


def _judgment(decision: str, confidence: float) -> dict[str, Any]:
    met = (
        [MetRequirement(requirement="python", evidence_quote="6 years building backends")]
        if decision == "qualified"
        else []
    )
    return FitJudgment(
        decision=decision,  # type: ignore[arg-type]  # reason: test feeds the Literal value
        confidence=confidence,
        met_requirements=met,
        missing_hard_requirements=[],
        failed_dealbreakers=[],
        rationale="r",
    ).model_dump()


def _job(outcome: str, i: int) -> dict[str, Any]:
    # outcome is encoded in the redirect_url so the faked subgraph stages can branch
    return {
        "id": f"job-{i}",
        "title": f"Engineer {i}",
        "company": {"display_name": "Acme Inc"},
        "location": {"display_name": "Austin, TX"},
        "redirect_url": f"https://adzuna.example/land/{i}/{outcome}",
        "description": "snippet",
    }


def _valid_ej() -> dict[str, Any]:
    """A fully-valid EvaluatedJob payload that passes ``is_qualified``."""
    return {
        "job_id": "job-0",
        "title": "Engineer",
        "company": "Acme Inc",
        "location": "Austin, TX",
        "final_url": "https://employer.example/job",
        "source": _job("qualified", 0),
        "jd_char_len": 800,
        "requirements": _req().model_dump(),
        "judgment": _judgment("qualified", 0.9),
        "status": "qualified",
        "failure_stage": None,
    }


def _eval_state(decision: str, confidence: float) -> dict[str, Any]:
    """A JobEvalState reaching ``finalize`` (success path) for ``_evaluated_job``."""
    return {
        "job": _job("x", 0),
        "profile": _profile().model_dump(),
        "final_url": "https://employer.example/job",
        "fetched": None,
        "jd_text": None,
        "jd_quality": {"char_len": 800, "passed": True, "reasons": []},
        "requirements": _req().model_dump(),
        "judgment": _judgment(decision, confidence),
        "result": [],
        "evaluated_jobs": [],
        "errors": [],
    }


# --- faked subgraph stages (branch on the outcome encoded in the url) ---------
def _fake_resolve(redirect_url: str) -> str:
    if "fail_resolve" in redirect_url:
        raise JDParserError(code="RESOLVE_FAILED", message="no final url")
    return redirect_url


def _fake_fetch(url: str) -> FetchResult:
    return FetchResult(url=url, status=200, html="<html></html>", source="http")


def _fake_extract(fetched: FetchResult) -> str | None:
    if "fail_extract" in fetched.url:
        return None
    outcome = fetched.url.rsplit("/", 1)[-1]
    return f"OUTCOME={outcome}\n" + ("lorem ipsum dolor sit amet " * 40)


def _fake_quality(text: str) -> QualityResult:
    return QualityResult(char_len=len(text), passed=True, reasons=[])


def _fake_parse(jd_text: str) -> JobRequirements:
    outcome = jd_text.split("OUTCOME=", 1)[1].split("\n", 1)[0]
    req = _req()
    return req.model_copy(update={"required_skills": [outcome]})


def _fake_judge(
    profile: ResumeProfile, requirements: JobRequirements, job_title: str = ""
) -> FitJudgment:
    outcome = requirements.required_skills[0]
    if outcome == "qualified":
        return FitJudgment(
            decision="qualified",
            confidence=0.9,
            met_requirements=[MetRequirement(requirement="python", evidence_quote="6 years building backends")],
            missing_hard_requirements=[],
            failed_dealbreakers=[],
            rationale="strong fit",
        )
    if outcome == "uncertain":
        return FitJudgment(
            decision="uncertain",
            confidence=0.9,
            met_requirements=[],
            missing_hard_requirements=[],
            failed_dealbreakers=[],
            rationale="unclear",
        )
    return FitJudgment(
        decision="not_qualified",
        confidence=0.9,
        met_requirements=[],
        missing_hard_requirements=["distributed systems"],
        failed_dealbreakers=[],
        rationale="missing reqs",
    )


def _initial_state(jobs_ignored: object) -> JobMatchState:
    return {
        "run_id": str(uuid4()),
        "user_id": "local",
        "resume_file_path": RESUME,
        "resume_text": None,
        "resume_fingerprint": None,
        "resume_profile_id": None,
        "resume_profile": None,
        "resume_cache_hit": False,
        "search_plan": None,
        "adzuna_results": [],
        "deduped_jobs": [],
        "evaluated_jobs": [],
        "qualified_jobs": [],
        "errors": [],
    }


def run_graph(
    monkeypatch: pytest.MonkeyPatch,
    jobs: list[dict[str, Any]],
    *,
    cache_hit: bool = True,
    profile_counter: dict[str, int] | None = None,
    resolve: Any = _fake_resolve,
    fetch_fn: Any = _fake_fetch,
    extract: Any = _fake_extract,
    quality: Any = _fake_quality,
    parse: Any = _fake_parse,
    judge: Any = _fake_judge,
) -> dict[str, Any]:
    """Patch the whole pipeline (no network) and invoke the REAL graph."""
    # top-level nodes
    stored = _stored_profile() if cache_hit else None
    monkeypatch.setattr("jdparser.graph.nodes.get_profile", lambda key: stored)

    def _pr(text: str) -> ResumeProfile:
        if profile_counter is not None:
            profile_counter["n"] += 1
        return _profile()

    monkeypatch.setattr("jdparser.graph.nodes.profile_resume", _pr)
    monkeypatch.setattr("jdparser.graph.nodes.put_profile", lambda rec: None)
    monkeypatch.setattr(
        "jdparser.graph.nodes.plan_adzuna_queries", lambda profile: [AdzunaQuery(what="engineer")]
    )
    monkeypatch.setattr("jdparser.graph.nodes.run_search_plan", lambda plan: list(jobs))
    # subgraph stages
    monkeypatch.setattr("jdparser.graph.subgraph.resolve_final_url", resolve)
    monkeypatch.setattr("jdparser.graph.subgraph.fetch", fetch_fn)
    monkeypatch.setattr("jdparser.graph.subgraph.extract_jd_text", extract)
    monkeypatch.setattr("jdparser.graph.subgraph.check_quality", quality)
    monkeypatch.setattr("jdparser.graph.subgraph.parse_jd_requirements", parse)
    monkeypatch.setattr("jdparser.graph.subgraph.judge_fit", judge)

    graph = build_graph()
    state = _initial_state(jobs)
    config = {
        "max_concurrency": EVAL_FANOUT_CONCURRENCY,
        "configurable": {"thread_id": state["run_id"]},
    }
    result: dict[str, Any] = graph.invoke(state, config=config)
    return result


# --- §7.6 / §3.10: count invariant + display-subset determinism --------------
def test_count_invariant_and_display_is_qualified_subset(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs = [
        _job("qualified", 0),
        _job("rejected", 1),
        _job("uncertain", 2),
        _job("fail_resolve", 3),
        _job("fail_extract", 4),
    ]
    result = run_graph(monkeypatch, jobs)

    assert len(result["evaluated_jobs"]) == len(result["deduped_jobs"]) == 5
    # §7.6: the visible set is exactly is_qualified() applied to evaluated_jobs.
    assert result["qualified_jobs"] == [ej for ej in result["evaluated_jobs"] if is_qualified(ej)]
    assert len(result["qualified_jobs"]) == 1
    assert result["qualified_jobs"][0]["status"] == "qualified"
    # statuses cover the §3.9 spread
    statuses = sorted(ej["status"] for ej in result["evaluated_jobs"])
    assert statuses == ["failed", "failed", "not_qualified", "qualified", "uncertain"]


# --- §7.1: is_qualified unit gates -------------------------------------------
def test_is_qualified_units() -> None:
    assert is_qualified(_valid_ej()) is True

    # null requirements excludes even with a qualified judgment present
    ej = _valid_ej()
    ej["requirements"] = None
    assert is_qualified(ej) is False

    # null / empty final_url excludes
    assert is_qualified({**_valid_ej(), "final_url": None}) is False
    assert is_qualified({**_valid_ej(), "final_url": ""}) is False

    # jd_char_len below MIN_JD_CHARS excludes
    assert is_qualified({**_valid_ej(), "jd_char_len": MIN_JD_CHARS - 1}) is False
    assert is_qualified({**_valid_ej(), "jd_char_len": None}) is False


# --- §7.1: real-graph forced parse failure -----------------------------------
def test_forced_parse_failure_real_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(jd_text: str) -> JobRequirements:
        raise JDParserError(code="PARSE_INVALID", message="bad json")

    result = run_graph(monkeypatch, [_job("qualified", 0)], parse=boom)
    ej = result["evaluated_jobs"][0]
    assert ej["status"] == "failed"
    assert ej["failure_stage"] == "parse"
    assert ej["requirements"] is None
    assert result["qualified_jobs"] == []
    assert any(e["stage"] == "parse" and e["code"] == "PARSE_INVALID" for e in result["errors"])


# --- §7.2: dealbreaker / missing-hard-requirement gate -----------------------
def test_fit_gate_excludes_dealbreakers_and_missing() -> None:
    ej = _valid_ej()  # decision qualified, confidence 0.9
    ej["judgment"]["failed_dealbreakers"] = ["active TS/SCI clearance"]
    assert is_qualified(ej) is False

    ej2 = _valid_ej()
    ej2["judgment"]["missing_hard_requirements"] = ["kubernetes"]
    assert is_qualified(ej2) is False


# --- §7.3: confidence gate via _evaluated_job status derivation --------------
def test_confidence_gate() -> None:
    low = _evaluated_job(_eval_state("qualified", 0.6))  # type: ignore[arg-type]  # reason: JobEvalState payload
    assert low["status"] == "uncertain"
    assert is_qualified(low) is False

    high = _evaluated_job(_eval_state("qualified", 0.8))  # type: ignore[arg-type]  # reason: JobEvalState payload
    assert high["status"] == "qualified"
    assert is_qualified(high) is True


# --- §7.4: cache hit skips the profiler --------------------------------------
def test_cache_hit_skips_profiler(monkeypatch: pytest.MonkeyPatch) -> None:
    counter = {"n": 0}
    result = run_graph(monkeypatch, [_job("qualified", 0)], cache_hit=True, profile_counter=counter)
    assert result["resume_cache_hit"] is True
    assert counter["n"] == 0  # profile_resume NOT called


def test_cache_miss_calls_profiler_once(monkeypatch: pytest.MonkeyPatch) -> None:
    counter = {"n": 0}
    result = run_graph(monkeypatch, [_job("qualified", 0)], cache_hit=False, profile_counter=counter)
    assert result["resume_cache_hit"] is False
    assert counter["n"] == 1  # profile_resume called exactly once


# --- §7.x: forced extract failure → failed/extract ---------------------------
def test_forced_extract_failure_real_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    result = run_graph(monkeypatch, [_job("qualified", 0)], extract=lambda fetched: None)
    ej = result["evaluated_jobs"][0]
    assert ej["status"] == "failed"
    assert ej["failure_stage"] == "extract"
    assert ej["requirements"] is None
    assert result["qualified_jobs"] == []
    assert any(e["stage"] == "extract" and e["code"] == "JD_NOT_FOUND" for e in result["errors"])


# --- §3.10: EVAL_COUNT_MISMATCH (reducer count != deduped count) -------------
def test_eval_count_mismatch_raises() -> None:
    state: dict[str, Any] = {
        "evaluated_jobs": [_valid_ej()],
        "deduped_jobs": [{"id": "a"}, {"id": "b"}],
    }
    with pytest.raises(JDParserError) as exc:
        aggregate_matches(state)  # type: ignore[arg-type]  # reason: partial JobMatchState for the unit
    assert exc.value.code == "EVAL_COUNT_MISMATCH"
