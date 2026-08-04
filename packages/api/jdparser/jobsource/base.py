"""The job-source seam — what the pipeline needs from a place that supplies job postings.

Everything provider-specific about discovery lives behind this protocol: the query
language, the planner prompt that teaches an LLM to speak it, and the search call itself.
Everything downstream of `search()` speaks `Job` and knows nothing about who supplied it.

There is currently ONE implementation (Adzuna). That is deliberate and it is not an
oversight — see docs/REFACTOR_AUDIT.md §4.2 for why the seam was built ahead of a second
source, and §1.1 for the decision record. Do not collapse it back on the grounds that a
protocol with one implementation is redundant; that reasoning was considered and rejected
with the tradeoff in full view.

`planner_prompt` is the important member. The shallow coupling to a provider is its HTTP
client; the deep one is the ~90 lines of prompt teaching the planner that `what` ANDs its
terms, that `where` must be geographic or returns nothing, and that a query's result budget
is fixed regardless of area. A second source needs a different prompt far more than it needs
a different client.
"""

from typing import Any, Protocol

from pydantic import BaseModel

from jdparser.jobs import Job


class JobSource(Protocol):
    """A source of job postings."""

    #: Short identifier, e.g. "adzuna". Appears in error records and logs.
    name: str
    #: System prompt teaching the planner this provider's query language.
    planner_prompt: str

    @property
    def query_model(self) -> type[BaseModel]:
        """The provider's query schema — the planner emits these, `search` consumes them.

        Read-only (a property, not a plain attribute) so an implementation may narrow it to
        its own concrete query type: `type[...]` is invariant, so a settable
        `query_model: type[BaseModel]` would reject `type[AdzunaQuery]`.
        """
        ...

    def search(self, plan: list[Any]) -> list[Job]:
        """Run every query in ``plan`` and return normalized postings.

        Raises ``JDParserError`` on the first failure — the caller
        (``graph.nodes.search_jobs``) owns the continue-on-partial-failure policy.
        """
        ...
