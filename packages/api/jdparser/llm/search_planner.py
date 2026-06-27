"""search_planner — `plan_adzuna_queries` (logic node, GLM 5.2). SPEC §3.4 / §4.4.

Defaults (env-overridable, SPEC §6.3): GLM 5.2, temp 0.3, max_tokens 4000,
reasoning off. response_model is the `SearchPlan` wrapper; returns its `queries`
truncated to SEARCH_PLAN_MAX_QUERIES. Maps validation failure -> PLAN_INVALID.
"""

from jdparser.config import LLM_NODES, SEARCH_PLAN_MAX_QUERIES
from jdparser.llm.client import _call
from jdparser.llm.schemas import AdzunaQuery, ResumeProfile, SearchPlan

_SYSTEM = """\
You are an Adzuna job-search planner. Given a candidate's ResumeProfile (JSON), \
produce a SearchPlan: a list of distinct AdzunaQuery objects that together cover \
the candidate's realistic target jobs.

Rules:
- Expand role synonyms across `profile.roles` (e.g. "backend engineer" -> also \
"software engineer", "backend developer") so each query's `what` targets a real \
role variant; avoid near-duplicate queries.
- Map preferred locations from `profile.locations` to `where`, and set a sensible \
`distance` (km radius) around each `where`.
- Set the employment-type booleans (`full_time`, `part_time`, `contract`, \
`permanent`) from `profile.employment_types` when the candidate signals a preference.
- Use ONLY the fields defined by the AdzunaQuery schema. Inventing any other \
parameter is forbidden and will be rejected.
- Produce between 1 and %d queries, ordered most-relevant first.
""" % SEARCH_PLAN_MAX_QUERIES


def plan_adzuna_queries(profile: ResumeProfile) -> list[AdzunaQuery]:
    user = profile.model_dump_json()
    result = _call(LLM_NODES["planner"], _SYSTEM, user, SearchPlan, "PLAN_INVALID")
    return result.queries[:SEARCH_PLAN_MAX_QUERIES]
