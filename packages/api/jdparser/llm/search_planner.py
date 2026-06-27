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
the candidate's realistic target jobs. The Adzuna `what` field is a keyword search \
that ANDs all its terms, and `where` matches GEOGRAPHIC PLACES ONLY.

Rules:
- `what` must be BROAD: one short role phrase, 1-3 words (e.g. "backend engineer", \
"python engineer", "platform engineer", "backend developer"). Do NOT stack seniority \
words or multiple skills into one `what` (e.g. NOT "Senior Backend Engineer Python \
Go") — long `what` strings AND every term and match almost nothing. Seniority and \
specific skills are judged later from the full job description, not via `what`.
- Expand role synonyms across `profile.roles` so each query targets a different real \
role variant; avoid near-duplicate `what` values.
- `where` is a REAL geographic location ONLY (a US city/metro/state, e.g. "Austin, \
TX"). NEVER put "Remote", "remote", "Anywhere", "Remote (US)", or any non-place \
string in `where` — Adzuna returns ZERO results for a non-geographic `where`. If a \
location preference is remote/non-geographic, OMIT `where` entirely for that query \
(this performs a nationwide search). Set `distance` (km) only alongside a real `where`.
- ALWAYS include at least one nationwide query (no `where` at all) so remote-friendly \
candidates get broad coverage.
- Keep the candidate pool tractable and high-signal: use `results_per_page` = 20 and \
`pages` = 1 for each query (an LLM evaluates every returned job downstream, so a \
focused pool beats a huge one). Produce EXACTLY 3 queries — a couple of role variants \
plus at least one nationwide (no-`where`) query.
- Set employment-type booleans (`full_time`, etc.) ONLY when the candidate clearly \
signals one preferred type, and use them sparingly — they drop jobs Adzuna hasn't \
tagged.
- Use ONLY the fields defined by the AdzunaQuery schema. Inventing any other \
parameter is forbidden and will be rejected.
- Produce between 1 and %d queries, ordered most-relevant first.
""" % SEARCH_PLAN_MAX_QUERIES


def plan_adzuna_queries(profile: ResumeProfile) -> list[AdzunaQuery]:
    user = profile.model_dump_json()
    result = _call(LLM_NODES["planner"], _SYSTEM, user, SearchPlan, "PLAN_INVALID")
    return result.queries[:SEARCH_PLAN_MAX_QUERIES]
