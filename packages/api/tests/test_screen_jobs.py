"""The relevance pre-screen — SPEC §4.1 (screen_jobs).

Three behaviors in one node: the coarse same-field filter, the recruitment-agency
dimension, and the SCREEN_EVAL_CAP ceiling (which must hold even when the screen errors).
"""

from typing import Any

import pytest

from graph_harness import _job, run_graph
from jdparser.config import JDParserError
from jdparser.llm.schemas import JobScreen, ResumeProfile
def test_recruitment_agency_screened_out_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Agency listings are a relevance dimension: screened out (never evaluated) by default,
    landing in screened_out with reason 'agency'."""
    jobs = [_job("qualified", 0), _job("qualified", 1)]

    def _screen(profile: ResumeProfile, js: list[dict[str, Any]]) -> JobScreen:
        return JobScreen(relevant_job_ids=["job-0", "job-1"], agency_job_ids=["job-1"])

    result = run_graph(monkeypatch, jobs, screen=_screen)  # include_agencies defaults False
    assert {j["job_id"] for j in result["evaluated_jobs"]} == {"job-0"}   # agency NOT evaluated
    assert {j["job_id"] for j in result["qualified_jobs"]} == {"job-0"}
    agency_out = [s for s in result["screened_out"] if s["reason"] == "agency"]
    assert {s["job_id"] for s in agency_out} == {"job-1"}


def test_recruitment_agency_included_and_badged(monkeypatch: pytest.MonkeyPatch) -> None:
    """With include_agencies the agency listing IS evaluated and carries the badge flag
    (is_recruitment_agency) through to its EvaluatedJob."""
    jobs = [_job("qualified", 0), _job("qualified", 1)]

    def _screen(profile: ResumeProfile, js: list[dict[str, Any]]) -> JobScreen:
        return JobScreen(relevant_job_ids=["job-0", "job-1"], agency_job_ids=["job-1"])

    result = run_graph(monkeypatch, jobs, screen=_screen, include_agencies=True)
    assert {j["job_id"] for j in result["qualified_jobs"]} == {"job-0", "job-1"}  # both evaluated
    flagged = {j["job_id"]: j["is_recruitment_agency"] for j in result["evaluated_jobs"]}
    assert flagged == {"job-0": False, "job-1": True}  # only the agency is badged


def test_relevance_screen_filters_off_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """The screen drops off-field jobs before the fan-out; only kept jobs are evaluated,
    the dropped ones land in screened_out, and the §3.10 count invariant still holds."""
    jobs = [_job("qualified", 0), _job("not_qualified", 1), _job("not_qualified", 2)]

    def _screen(profile: ResumeProfile, js: list[dict[str, Any]]) -> JobScreen:
        return JobScreen(relevant_job_ids=["job-0"])  # keep only job-0

    result = run_graph(monkeypatch, jobs, screen=_screen)
    assert len(result["evaluated_jobs"]) == 1
    assert len(result["deduped_jobs"]) == 1  # pool filtered to the kept job
    assert {j["job_id"] for j in result["screened_out"]} == {"job-1", "job-2"}
    assert all(j["title"] for j in result["screened_out"])


def test_relevance_screen_keeps_all_when_it_would_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs = [_job("qualified", 0), _job("qualified", 1)]

    def _screen(profile: ResumeProfile, js: list[dict[str, Any]]) -> JobScreen:
        return JobScreen(relevant_job_ids=[])  # drops everything -> guard keeps all

    result = run_graph(monkeypatch, jobs, screen=_screen)
    assert len(result["evaluated_jobs"]) == 2
    assert result["screened_out"] == []


def test_screen_caps_eval_pool_to_top_n(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bounded funnel caps how many in-field jobs reach the fan-out, keeping the
    screener's TOP-ranked survivors; the overflow lands in screened_out (over_cap)."""
    monkeypatch.setattr("jdparser.graph.nodes.SCREEN_EVAL_CAP", 2)
    jobs = [_job("qualified", i) for i in range(4)]

    # screener ranks most-relevant-first (deliberately NOT the input order)
    def _screen(profile: ResumeProfile, js: list[dict[str, Any]]) -> JobScreen:
        return JobScreen(relevant_job_ids=["job-2", "job-3", "job-0", "job-1"])

    result = run_graph(monkeypatch, jobs, screen=_screen)
    assert len(result["evaluated_jobs"]) == 2                       # only the cap is evaluated
    assert [j["job_id"] for j in result["evaluated_jobs"]] == ["job-2", "job-3"] or \
        {j["job_id"] for j in result["evaluated_jobs"]} == {"job-2", "job-3"}  # top-2 by rank
    over = result["screened_out"]
    assert {j["job_id"] for j in over} == {"job-0", "job-1"}        # ranks 3-4 deferred
    assert all(j["reason"] == "over_cap" for j in over)


def test_screen_cap_applies_even_when_screen_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cap is the timeout guard, so it holds even on the screen-failure fallback."""
    monkeypatch.setattr("jdparser.graph.nodes.SCREEN_EVAL_CAP", 2)
    jobs = [_job("qualified", i) for i in range(4)]

    def _boom(profile: ResumeProfile, js: list[dict[str, Any]]) -> JobScreen:
        raise JDParserError(code="SCREEN_INVALID", message="boom")

    result = run_graph(monkeypatch, jobs, screen=_boom)
    assert len(result["evaluated_jobs"]) == 2                       # full pool, but still capped
    assert any(e["stage"] == "screen" for e in result["errors"])   # failure recorded, run survives


def test_relevance_screen_error_keeps_all(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs = [_job("qualified", 0), _job("qualified", 1)]

    def _screen(profile: ResumeProfile, js: list[dict[str, Any]]) -> JobScreen:
        raise JDParserError(code="SCREEN_INVALID", message="boom")

    result = run_graph(monkeypatch, jobs, screen=_screen)
    assert len(result["evaluated_jobs"]) == 2  # screen failure -> pool intact
    assert any(e["stage"] == "screen" for e in result["errors"])
