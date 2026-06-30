"""search_planner — `plan_adzuna_queries` (logic node, gemini-3.1-flash-lite). SPEC §3.4 / §4.4.

Defaults (env-overridable, SPEC §6.3): gemini-3.1-flash-lite, temp 0.3, max_tokens 4000,
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
- `what` must be a BROAD role phrase (1-3 words) IN THE VOCABULARY OF THE CANDIDATE'S \
ACTUAL FIELD. Infer the real function/field from `skills` and `domains` — do NOT just \
echo the literal `roles` titles. Many job titles are industry-ambiguous, and a bare \
keyword match crosses into the WRONG industry. Worked example: a "Product Assurance \
Specialist" whose skills are test plans, regression/exploratory testing, Jira, and \
defect tracking is a SOFTWARE QA tester — search "qa analyst", "software tester", \
"qa engineer", "quality assurance analyst"; do NOT search "product assurance" (Adzuna \
matches that to food-safety / grocery roles) or "quality assurance" alone (too \
cross-industry). Pick terms that keep results inside the candidate's real field.
- Do NOT stack seniority words or multiple skills into one `what` (e.g. NOT "Senior \
Backend Engineer Python Go") — long ANDed `what` strings match almost nothing. \
Seniority and specific skills are judged later from the full job description.
- Produce role-variant queries for the candidate's CURRENT / primary field (synonyms \
welcome), avoiding near-duplicate `what` values. Do NOT search roles from a PRIOR \
career the candidate has clearly moved on from (e.g. an old "Lighting Designer" role \
for someone now working in software QA) unless the resume signals it as a current target.
- You MAY use `what_or` (matches ANY of its space-separated terms) on 1-2 queries to \
cover several closely-related title synonyms at once — but SCOPE IT CAREFULLY. Adzuna \
ANDs `what` with the `what_or` group, so ALWAYS pair `what_or` with an anchoring `what` \
term (the core field token). Example for software QA: `what="qa"`, \
`what_or="analyst engineer tester sdet"` → matches jobs containing "qa" AND any of \
those. NEVER put generic words alone in `what_or` (e.g. "engineer", "analyst", \
"manager", "automation", "quality", "developer") — without the `what` anchor they match \
millions of unrelated jobs. Keep the other queries as plain `what` role variants.
- `where` is a REAL geographic location ONLY (a US city/metro/state, e.g. "Austin, \
TX"). NEVER put "Remote", "remote", "Anywhere", "Remote (US)", or any non-place \
string in `where` — Adzuna returns ZERO results for a non-geographic `where`. If a \
location preference is remote/non-geographic, OMIT `where` entirely for that query \
(this performs a nationwide search). Set `distance` (km) only alongside a real `where`.
- ALWAYS include at least one nationwide query (no `where` at all) so remote-friendly \
candidates get broad coverage.
- Use `results_per_page` = 50 and `pages` = 1 for each query. Produce 5-6 queries: \
several distinct role variants across the candidate's field (some `where`-scoped, at \
least one nationwide), optionally using `what_or` on 1-2 of them for synonym coverage. \
A cheap relevance screen filters these before the expensive evaluation, so broader \
title coverage is good — just keep each query in-field.
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
