"""Per-run LLM token/cost accounting.

Every OpenRouter call in a run lands here, including the ones instructor makes when it
re-asks after a validation failure — those are billed like any other call, and counting
only the successful attempt would understate exactly the runs that cost the most.

Attribution uses a `ContextVar`. LangGraph's fan-out evaluates jobs on a thread pool, and
`contextvars` propagate into those workers (verified against langgraph 0.6.11), so a
single accumulator set once in the API's background task collects the whole run: top-level
nodes, per-job workers, and retries. Concurrent runs each get their own accumulator rather
than sharing a global.

The sharp edge: that propagation is langchain-core copying the context into its executor,
NOT something threads do on their own — a bare `threading.Thread` inherits nothing and its
calls go uncounted, silently. Both facts are pinned in tests/test_usage.py. If an LLM call
ever needs to run from a hand-rolled thread, copy the context into it explicitly.

Cost comes from OpenRouter, not from a price table here. Asking for it (`usage.include`)
returns what was actually charged, which stays correct when a model's price changes or
when OpenRouter routes to a different upstream provider. A local table would be a second
source of truth that silently goes stale.
"""

from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Lock
from typing import Any


@dataclass
class RunUsage:
    """Totals for one run. Mutated from several threads, so guard with ``_lock``."""

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    # False once any call came back without a cost figure, so the UI can say "at least"
    # rather than presenting a partial total as complete.
    cost_complete: bool = True
    _lock: Lock = field(default_factory=Lock, repr=False, compare=False)

    def record(self, prompt: int, completion: int, cost: float | None) -> None:
        with self._lock:
            self.calls += 1
            self.prompt_tokens += prompt
            self.completion_tokens += completion
            if cost is None:
                self.cost_complete = False
            else:
                self.cost_usd += cost

    def as_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "calls": self.calls,
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "cost_usd": round(self.cost_usd, 6),
                "cost_complete": self.cost_complete,
            }


_current: ContextVar[RunUsage | None] = ContextVar("llm_usage", default=None)


def start_run_usage() -> RunUsage:
    """Begin accounting for the current context; returns the accumulator to read later."""
    usage = RunUsage()
    _current.set(usage)
    return usage


def record_completion(completion: Any) -> None:  # reason: raw provider completion object
    """Record one billed call. A no-op outside a run (CLI, tests, direct agent calls).

    Reads cost from the provider's `usage` block. The OpenAI SDK's typed `CompletionUsage`
    has no `cost` field, so OpenRouter's lands in `model_extra` — check both rather than
    assuming which, since that depends on SDK version.
    """
    usage = _current.get()
    if usage is None:
        return
    raw = getattr(completion, "usage", None)
    if raw is None:
        return
    extra = getattr(raw, "model_extra", None) or {}
    cost = getattr(raw, "cost", None)
    if cost is None:
        cost = extra.get("cost")
    usage.record(
        prompt=getattr(raw, "prompt_tokens", 0) or 0,
        completion=getattr(raw, "completion_tokens", 0) or 0,
        cost=float(cost) if cost is not None else None,
    )
