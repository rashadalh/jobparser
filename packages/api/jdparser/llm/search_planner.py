"""search_planner — `plan_queries` (logic node, gemini-3.1-flash-lite). SPEC §3.4 / §4.4.

Defaults (env-overridable, SPEC §6.3): gemini-3.1-flash-lite, temp 0.3, max_tokens 4000,
reasoning off. response_model is the `SearchPlan` wrapper; returns its `queries`
truncated to SEARCH_PLAN_MAX_QUERIES. Maps validation failure -> PLAN_INVALID.

This module owns the CALL. The provider's query dialect — the schema, and the prompt that
teaches an LLM to speak it — comes from the active `JobSource` (jobsource/base.py), because
a second provider needs a different prompt far more than it needs a different client.
"""

from jdparser.config import LLM_NODES, SEARCH_PLAN_MAX_QUERIES
from jdparser.jobsource import SOURCE
from jdparser.llm.client import _call
from jdparser.llm.schemas import AdzunaQuery, ResumeProfile, SearchPlan


def plan_queries(profile: ResumeProfile) -> list[AdzunaQuery]:
    """Ask the planner for a search plan in the active source's query language.

    The prompt comes from ``SOURCE``; the response schema is the concrete ``SearchPlan``
    wrapper. That wrapper stays concrete on purpose: instructor's structured output needs a
    single named schema with a concrete element type, so there is nothing to gain from
    annotating a generic ``BaseModel`` that would let a caller believe this function can
    return a query type it cannot actually produce. A second source brings its own wrapper.
    """
    user = profile.model_dump_json()
    result = _call(LLM_NODES["planner"], SOURCE.planner_prompt, user, SearchPlan, "PLAN_INVALID")
    return result.queries[:SEARCH_PLAN_MAX_QUERIES]
