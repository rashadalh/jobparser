"""Per-run LLM cost accounting (REFACTOR: run cost estimate).

The value of this feature rests on one assumption: that a ContextVar set before
`graph.invoke` is visible inside LangGraph's fan-out worker threads. If it isn't, every
per-job call (the overwhelming majority of a run's spend) is silently dropped and the
reported cost is a small fraction of the real one — wrong in the direction nobody
notices. `test_usage_survives_the_threaded_fanout` is the test that matters here.
"""

import operator
import threading
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from jdparser.llm.usage import record_completion, start_run_usage


class _Usage:
    """Stands in for the provider's usage block, cost included."""

    def __init__(self, prompt: int, completion: int, cost: float | None) -> None:
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        if cost is not None:
            self.cost = cost
        self.model_extra: dict[str, Any] = {}


class _Completion:
    def __init__(self, prompt: int = 10, completion: int = 5, cost: float | None = 0.001) -> None:
        self.usage = _Usage(prompt, completion, cost)


def test_records_tokens_and_cost() -> None:
    usage = start_run_usage()
    record_completion(_Completion(prompt=100, completion=20, cost=0.004))
    record_completion(_Completion(prompt=50, completion=10, cost=0.002))

    assert usage.as_dict() == {
        "calls": 2,
        "prompt_tokens": 150,
        "completion_tokens": 30,
        "cost_usd": 0.006,
        "cost_complete": True,
    }


def test_cost_from_model_extra_when_sdk_lacks_the_field() -> None:
    """OpenRouter's `cost` is not in the OpenAI SDK's typed CompletionUsage, so depending
    on SDK version it arrives in `model_extra` instead. Both must work."""
    usage = start_run_usage()
    c = _Completion(cost=None)
    c.usage.model_extra = {"cost": 0.0025}
    record_completion(c)
    assert usage.as_dict()["cost_usd"] == 0.0025
    assert usage.as_dict()["cost_complete"] is True


def test_missing_cost_marks_the_total_incomplete() -> None:
    """A total that silently omits calls would read as authoritative. Flag it instead."""
    usage = start_run_usage()
    record_completion(_Completion(cost=0.003))
    record_completion(_Completion(cost=None))

    totals = usage.as_dict()
    assert totals["calls"] == 2
    assert totals["cost_usd"] == 0.003
    assert totals["cost_complete"] is False


def test_recording_outside_a_run_is_a_noop() -> None:
    """The CLI and direct agent calls have no accumulator; they must not blow up."""
    from jdparser.llm import usage as usage_mod

    usage_mod._current.set(None)
    record_completion(_Completion())  # must not raise


def test_completion_without_usage_block_is_ignored() -> None:
    usage = start_run_usage()

    class _Bare:
        usage = None

    record_completion(_Bare())
    assert usage.as_dict()["calls"] == 0


# --- the assumption the whole feature stands on ------------------------------
class _S(TypedDict):
    items: list[int]
    done: Annotated[list[str], operator.add]


def test_usage_survives_the_threaded_fanout() -> None:
    """Per-job LLM calls happen on LangGraph's worker threads, not the caller's.

    contextvars propagate into those workers, so one accumulator set before invoke()
    collects the whole run. If this ever stops holding, reported cost silently drops to
    just the top-level nodes — a fraction of the truth, with nothing else failing.
    """
    def fan(state: _S) -> list[Send]:
        return [Send("work", {"n": n}) for n in state["items"]]

    def work(state: dict[str, Any]) -> dict[str, Any]:
        record_completion(_Completion(prompt=10, completion=5, cost=0.001))
        return {"done": [threading.current_thread().name]}

    def join(state: _S) -> dict[str, Any]:
        return {}

    g: StateGraph[Any, Any, Any, Any] = StateGraph(_S)
    g.add_node("work", work)
    g.add_node("join", join)
    g.add_conditional_edges(START, fan, ["work"])
    g.add_edge("work", "join")
    g.add_edge("join", END)
    graph = g.compile()

    usage = start_run_usage()
    record_completion(_Completion(prompt=100, completion=50, cost=0.01))  # a top-level node
    graph.invoke({"items": list(range(8)), "done": []}, config={"max_concurrency": 4})

    totals = usage.as_dict()
    assert totals["calls"] == 9              # 1 top-level + 8 fanned-out
    assert totals["prompt_tokens"] == 180    # 100 + 8*10
    assert totals["cost_usd"] == 0.018       # 0.01 + 8*0.001


def test_concurrent_recording_loses_nothing() -> None:
    """Workers record concurrently; the lock means counts can't tear.

    Calls `record` directly rather than going through `record_completion`: a bare
    `threading.Thread` does NOT inherit the caller's context (see the test below), so
    routing through the ContextVar here would measure thread setup, not the lock.
    """
    usage = start_run_usage()

    def hammer() -> None:
        for _ in range(200):
            usage.record(prompt=1, completion=1, cost=0.0001)

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert usage.as_dict()["calls"] == 1600
    assert usage.as_dict()["prompt_tokens"] == 1600


def test_a_bare_thread_does_not_inherit_the_accumulator() -> None:
    """Documents the sharp edge this design sits on.

    contextvars do NOT cross into a plain `threading.Thread`; the fan-out only works
    because langchain-core copies the context into its executor. Anyone who later runs an
    LLM call from a hand-rolled thread will get silent under-counting, not an error, so
    this pins the behavior rather than leaving it to be rediscovered.
    """
    usage = start_run_usage()

    def worker() -> None:
        record_completion(_Completion())

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    assert usage.as_dict()["calls"] == 0  # not 1 — the bare thread saw no accumulator
