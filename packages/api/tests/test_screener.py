"""screen_relevance_batched tests — chunking/merge/partial-failure behavior.

ZERO network: `screen_relevance` itself is monkeypatched per-call, so these only
exercise the batching/merge logic in `llm/screener.py`.
"""

import pytest

from jdparser.config import JDParserError
from jdparser.llm import screener as screener_mod
from jdparser.llm.schemas import JobScreen, ResumeProfile
from builders import profile as _profile


def _jobs(n: int) -> list[dict[str, str]]:
    return [{"id": str(i), "title": "engineer", "description": "x"} for i in range(n)]


def test_single_batch_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pool within SCREEN_BATCH_SIZE makes exactly one call and passes results through."""
    calls = []

    def fake(profile: ResumeProfile, jobs: list[dict[str, str]]) -> JobScreen:
        calls.append(jobs)
        return JobScreen(relevant_job_ids=["0", "1"], agency_job_ids=["1"])

    monkeypatch.setattr(screener_mod, "screen_relevance", fake)
    result = screener_mod.screen_relevance_batched(_profile(), _jobs(2))
    assert len(calls) == 1
    assert result.relevant_job_ids == ["0", "1"]
    assert result.agency_job_ids == ["1"]


def test_batches_round_robin_merge(monkeypatch: pytest.MonkeyPatch) -> None:
    """Multiple batches interleave round-robin, not concatenate — a job's batch is just
    dedup order, not real priority, so the first batch must not monopolize the ranking."""
    monkeypatch.setattr(screener_mod, "SCREEN_BATCH_SIZE", 2)
    batch_results = [
        JobScreen(relevant_job_ids=["a1", "a2", "a3"], agency_job_ids=["a2"]),
        JobScreen(relevant_job_ids=["b1"], agency_job_ids=[]),
    ]
    calls = iter(batch_results)

    def fake(profile: ResumeProfile, jobs: list[dict[str, str]]) -> JobScreen:
        return next(calls)

    monkeypatch.setattr(screener_mod, "screen_relevance", fake)
    result = screener_mod.screen_relevance_batched(_profile(), _jobs(4))
    # round-robin: a1, b1, (b batch exhausted), a2, a3
    assert result.relevant_job_ids == ["a1", "b1", "a2", "a3"]
    assert set(result.agency_job_ids) == {"a2"}


def test_one_batch_failure_keeps_the_rest(monkeypatch: pytest.MonkeyPatch) -> None:
    """One failed batch drops only its own jobs; the other batches' results still count."""
    monkeypatch.setattr(screener_mod, "SCREEN_BATCH_SIZE", 2)
    responses = iter(
        [
            JDParserError(code="SCREEN_INVALID", message="truncated"),
            JobScreen(relevant_job_ids=["b1", "b2"], agency_job_ids=[]),
        ]
    )

    def fake(profile: ResumeProfile, jobs: list[dict[str, str]]) -> JobScreen:
        item = next(responses)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(screener_mod, "screen_relevance", fake)
    result = screener_mod.screen_relevance_batched(_profile(), _jobs(4))
    assert result.relevant_job_ids == ["b1", "b2"]


def test_all_batches_fail_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every batch failing propagates the error (caller's existing whole-pool fallback)."""

    def fake(profile: ResumeProfile, jobs: list[dict[str, str]]) -> JobScreen:
        raise JDParserError(code="SCREEN_INVALID", message="truncated")

    monkeypatch.setattr(screener_mod, "screen_relevance", fake)
    with pytest.raises(JDParserError):
        screener_mod.screen_relevance_batched(_profile(), _jobs(3))
