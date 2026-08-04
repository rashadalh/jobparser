"""The Adzuna job source — everything provider-specific about discovery.

Implements `JobSource` (see ../base.py): Adzuna's query schema, the planner prompt that
teaches an LLM to speak Adzuna's query language, and the search call itself.

The prompt is the substance here. It encodes hard-won Adzuna behavior — `what` ANDs its
terms, `where` returns ZERO results for a non-geographic value, a query's result budget is
`results_per_page x pages` regardless of how large an area it covers — plus worked examples
for the failure mode where the planner echoes a resume's literal job title into a colliding
industry. None of that transfers to another provider.
"""

from jdparser.config import ADZUNA_MAX_PAGES, SEARCH_PLAN_MAX_QUERIES
from jdparser.jobs import Job
from jdparser.jobsource.adzuna.client import run_search_plan
from jdparser.llm.prompts import load
from jdparser.llm.schemas import AdzunaQuery

# Interpolated at import: the prompt must quote the SAME query/page budgets the code
# enforces, or the planner is told to produce a plan that gets truncated downstream.
PLANNER_PROMPT = load("planner") % {
    "max_pages": ADZUNA_MAX_PAGES,
    "max_queries": SEARCH_PLAN_MAX_QUERIES,
}


class AdzunaSource:
    """`JobSource` implementation for the Adzuna jobs API."""

    name = "adzuna"
    query_model = AdzunaQuery
    planner_prompt = PLANNER_PROMPT

    def search(self, plan: list[AdzunaQuery]) -> list[Job]:
        return run_search_plan(plan)
