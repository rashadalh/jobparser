"""Real-graph smoke tests — SPEC §7.6, §3.10.

Builds the REAL graph via `build_graph()` and invokes it: the count invariant, the
persisted-payload contract, forced per-stage failures, and the resume-cache paths.
Gate/screen/location behavior lives in test_display_gate / test_screen_jobs /
test_location_override.
"""

from typing import Any

import pytest

from graph_harness import _job, _raw, _valid_ej, run_graph
from jdparser.config import JDParserError
from jdparser.graph.nodes import aggregate_matches, is_qualified
from jdparser.jobsource.adzuna.client import _to_job
from jdparser.llm.schemas import JobRequirements


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


def test_evaluated_job_source_is_the_untouched_provider_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EvaluatedJob.source must be the raw provider dict, byte-for-byte.

    `source` is persisted into every data/runs/*.json. Introducing the Job model
    (REFACTOR_AUDIT Phase 2) must not change the on-disk shape — if `source` were built
    from Job's normalized fields instead of Job.raw, every historical run record would
    disagree with every new one and nothing would report an error.
    """
    raws = [_raw("qualified", 0), _raw("rejected", 1)]
    result = run_graph(monkeypatch, [_to_job(r) for r in raws])

    by_id = {ej["job_id"]: ej for ej in result["evaluated_jobs"]}
    for raw in raws:
        assert by_id[raw["id"]]["source"] == raw   # nested company/location dicts intact


# --- §7.1: real-graph forced parse failure -----------------------------------
def test_forced_parse_failure_real_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(jd_text: str, company: str = "") -> JobRequirements:
        raise JDParserError(code="PARSE_INVALID", message="bad json")

    result = run_graph(monkeypatch, [_job("qualified", 0)], parse=boom)
    ej = result["evaluated_jobs"][0]
    assert ej["status"] == "failed"
    assert ej["failure_stage"] == "parse"
    assert ej["requirements"] is None
    assert result["qualified_jobs"] == []
    assert any(e["stage"] == "parse" and e["code"] == "PARSE_INVALID" for e in result["errors"])


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
