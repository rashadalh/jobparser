"""Shared graph-test harness — builders, faked pipeline stages, and `run_graph`.

Imported by test_graph_smoke / test_display_gate / test_screen_jobs / test_location_override,
which used to be one 631-line file (REFACTOR_AUDIT F13).

ZERO live calls: every source / extract / LLM function is monkeypatched in the
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
from jdparser.graph.nodes import is_qualified
from jdparser.graph.state import JobMatchState
from builders import profile, stored_profile
from jdparser.jobsource import SOURCE
from jdparser.jobs import Job
from jdparser.jobsource.adzuna.client import _to_job
from jdparser.graph.subgraph import _evaluated_job
from jdparser.llm.schemas import (
    AdzunaQuery,
    CandidateNote,
    FetchResult,
    FitJudgment,
    JobRequirements,
    JobScreen,
    MetRequirement,
    QualityResult,
    ResumeEvidence,
    ResumeProfile,
    StoredResumeProfile,
)


def _fake_screen(profile: ResumeProfile, jobs: list[Job]) -> JobScreen:
    """Default screen fake: keep every job (relevance filtering tested separately)."""
    return JobScreen(relevant_job_ids=[j.id for j in jobs])

RESUME = str(Path(__file__).parent / "fixtures" / "sample_resume.pdf")


# --- fixtures / builders -----------------------------------------------------
def _profile() -> ResumeProfile:
    # skills/evidence are load-bearing here: _fake_judge quotes the evidence back as a
    # MetRequirement, so the strings must line up with the fake's expectations.
    return profile(
        skills=["python", "distributed systems"],
        total_years_experience=6.0,
        education=["M.S. Computer Science"],
        evidence=[ResumeEvidence(claim="6 years backend", source_quote="6 years building backends")],
    )


def _stored_profile() -> StoredResumeProfile:
    return stored_profile(id="seed-id", profile=_profile())


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
        thematic_fit=True,
        relevant_years_experience=6.0,
        thematic_rationale="r",
        decision=decision,  # type: ignore[arg-type]  # reason: test feeds the Literal value
        confidence=confidence,
        met_requirements=met,
        missing_hard_requirements=[],
        failed_dealbreakers=[],
        rationale="r",
    ).model_dump()


def _raw(outcome: str, i: int) -> dict[str, Any]:
    """A raw provider result, as the Adzuna API returns it."""
    # outcome is encoded in the redirect_url so the faked subgraph stages can branch
    return {
        "id": f"job-{i}",
        "title": f"Engineer {i}",
        "company": {"display_name": "Acme Inc"},
        "location": {"display_name": "Austin, TX"},
        "redirect_url": f"https://adzuna.example/land/{i}/{outcome}",
        "description": "snippet",
    }


def _job(outcome: str, i: int) -> Job:
    """The normalized Job the graph actually carries (see adzuna.client._to_job)."""
    return _to_job(_raw(outcome, i))


def _valid_ej() -> dict[str, Any]:
    """A fully-valid EvaluatedJob payload that passes ``is_qualified``."""
    return {
        "job_id": "job-0",
        "title": "Engineer",
        "company": "Acme Inc",
        "location": "Austin, TX",
        "final_url": "https://employer.example/job",
        "source": _raw("qualified", 0),
        "jd_char_len": 800,
        "requirements": _req().model_dump(),
        "judgment": _judgment("qualified", 0.9),
        "status": "qualified",
        "failure_stage": None,
    }


def _eval_state(decision: str, confidence: float) -> dict[str, Any]:
    """A JobEvalState reaching ``finalize`` (success path) for ``_evaluated_job``."""
    return {
        "job": _job("x", 0).model_dump(),
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


def _fake_parse(jd_text: str, company: str = "") -> JobRequirements:
    outcome = jd_text.split("OUTCOME=", 1)[1].split("\n", 1)[0]
    req = _req()
    return req.model_copy(update={"required_skills": [outcome]})


def _fake_judge(
    profile: ResumeProfile,
    requirements: JobRequirements,
    job_title: str = "",
    notes: list[CandidateNote] = [],
) -> FitJudgment:
    outcome = requirements.required_skills[0]
    if outcome == "qualified":
        return FitJudgment(
            thematic_fit=True,
            relevant_years_experience=6.0,
            thematic_rationale="same specialization",
            decision="qualified",
            confidence=0.9,
            met_requirements=[MetRequirement(requirement="python", evidence_quote="6 years building backends")],
            missing_hard_requirements=[],
            failed_dealbreakers=[],
            rationale="strong fit",
        )
    if outcome == "uncertain":
        return FitJudgment(
            thematic_fit=True,
            relevant_years_experience=6.0,
            thematic_rationale="same specialization",
            decision="uncertain",
            confidence=0.9,
            met_requirements=[],
            missing_hard_requirements=[],
            failed_dealbreakers=[],
            rationale="unclear",
        )
    return FitJudgment(
        thematic_fit=True,
        relevant_years_experience=6.0,
        thematic_rationale="same specialization",
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
        "resume_profile": None,
        "resume_cache_hit": False,
        "search_locations": None,
        "broaden_search": True,
        "max_days_old": None,
        "include_agencies": False,
        "search_plan": None,
        "job_results": [],
        "deduped_jobs": [],
        "screened_out": [],
        "evaluated_jobs": [],
        "qualified_jobs": [],
        "errors": [],
    }


def run_graph(
    monkeypatch: pytest.MonkeyPatch,
    jobs: list[Job],
    *,
    cache_hit: bool = True,
    profile_counter: dict[str, int] | None = None,
    resolve: Any = _fake_resolve,
    fetch_fn: Any = _fake_fetch,
    extract: Any = _fake_extract,
    quality: Any = _fake_quality,
    parse: Any = _fake_parse,
    judge: Any = _fake_judge,
    screen: Any = _fake_screen,
    include_agencies: bool = False,
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
        "jdparser.graph.nodes.plan_queries", lambda profile: [AdzunaQuery(what="engineer")]
    )
    monkeypatch.setattr(SOURCE, "search", lambda plan: list(jobs))
    monkeypatch.setattr("jdparser.graph.nodes.screen_relevance_batched", screen)
    # subgraph stages
    monkeypatch.setattr("jdparser.graph.subgraph.resolve_final_url", resolve)
    monkeypatch.setattr("jdparser.graph.subgraph.fetch", fetch_fn)
    monkeypatch.setattr("jdparser.graph.subgraph.extract_jd_text", extract)
    monkeypatch.setattr("jdparser.graph.subgraph.check_quality", quality)
    monkeypatch.setattr("jdparser.graph.subgraph.parse_jd_requirements", parse)
    monkeypatch.setattr("jdparser.graph.subgraph.judge_fit", judge)

    graph = build_graph()
    state = _initial_state(jobs)
    state["include_agencies"] = include_agencies
    config = {
        "max_concurrency": EVAL_FANOUT_CONCURRENCY,
        "configurable": {"thread_id": state["run_id"]},
    }
    result: dict[str, Any] = graph.invoke(state, config=config)
    return result
