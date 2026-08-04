"""screener — `screen_relevance` (relevance pre-screen, Gemini 3.1 Flash Lite).

A COARSE, cheap, same-field filter run BEFORE the expensive per-job evaluation
(resolve→fetch→extract→parse→judge). It reads only the title + snippet already on the
search result (no fetch) and returns the ids of jobs plausibly in the candidate's field,
so thematically-wrong jobs (a keyword collision like food-safety "Product Assurance"
for a software QA tester) never reach the fan-out. Validation failure -> SCREEN_INVALID.

The graph calls `screen_relevance_batched` (below), which chunks the deduped pool into
SCREEN_BATCH_SIZE-sized calls to `screen_relevance` — a single call's output token cost
scales with pool size (every relevant/agency id gets enumerated), so one call over an
unbounded pool eventually overflows max_tokens.
"""

import json
from itertools import zip_longest

from jdparser.config import LLM_NODES, SCREEN_BATCH_SIZE, JDParserError
from jdparser.jobs import Job
from jdparser.llm.client import _call
from jdparser.llm.prompts import load
from jdparser.llm.schemas import JobScreen, ResumeProfile

_SYSTEM = load("screener")


def screen_relevance(profile: ResumeProfile, jobs: list[Job]) -> JobScreen:
    """Coarse pre-screen over title/company/snippet: the in-field ``jobs`` (by id)
    RANKED most-relevant-first (caller caps to the top N — see SCREEN_EVAL_CAP), plus the
    ``agency_job_ids`` subset that look like recruitment-agency postings (caller drops
    those unless the run opted in)."""
    candidate = {
        "roles": profile.roles,
        "skills": profile.skills,
        "domains": profile.domains,
        "seniority": profile.seniority,
    }
    items = [
        {
            "id": job.id,
            "title": job.title,
            "company": job.company,
            "snippet": job.description[:200],
        }
        for job in jobs
    ]
    user = json.dumps({"candidate": candidate, "jobs": items})
    return _call(LLM_NODES["screener"], _SYSTEM, user, JobScreen, "SCREEN_INVALID")


def screen_relevance_batched(profile: ResumeProfile, jobs: list[Job]) -> JobScreen:
    """Chunk ``jobs`` into <= SCREEN_BATCH_SIZE batches and screen each independently.

    ``screen_relevance``'s output enumerates every relevant/agency id, so its token cost
    scales with pool size, not just prompt overhead — a wide nationwide pull (1000+ deduped
    jobs) overflows a single call's max_tokens and truncates (SCREEN_INVALID), which used to
    take out relevance filtering for the ENTIRE pool at once. Batching bounds the blast
    radius to one batch.

    Merge: relevant ids are ROUND-ROBIN interleaved across batches — a job's batch is just
    dedup order, not real priority, so concatenating batches in order would let whichever
    batch comes first monopolize the front of the ranking (and thus the SCREEN_EVAL_CAP
    cutoff). Agency ids are unioned (order doesn't matter there, it's a membership flag).

    A single batch's failure only drops THAT batch's jobs (ponytail: they fall out of
    relevant_job_ids and are bucketed "off_field" by the caller rather than distinctly
    flagged as a screen error — upgrade to a dedicated reason if batch failures stop being
    rare); only raises when EVERY batch fails, matching the caller's existing
    keep-the-whole-pool fallback for a total screen failure.
    """
    batches = [jobs[i : i + SCREEN_BATCH_SIZE] for i in range(0, len(jobs), SCREEN_BATCH_SIZE)]
    results: list[JobScreen] = []
    last_exc: JDParserError | None = None
    for batch in batches:
        try:
            results.append(screen_relevance(profile, batch))
        except JDParserError as e:
            last_exc = e
    if not results:
        assert last_exc is not None  # batches is non-empty when jobs is non-empty
        raise last_exc
    merged_relevant = [
        job_id
        for group in zip_longest(*(r.relevant_job_ids for r in results))
        for job_id in group
        if job_id is not None
    ]
    merged_agency = {job_id for r in results for job_id in r.agency_job_ids}
    return JobScreen(relevant_job_ids=merged_relevant, agency_job_ids=list(merged_agency))
