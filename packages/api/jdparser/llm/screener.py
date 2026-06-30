"""screener — `screen_relevance` (relevance pre-screen, Gemini 3.1 Flash Lite).

A COARSE, cheap, batched same-field filter run BEFORE the expensive per-job evaluation
(resolve→fetch→extract→parse→judge). It reads only the Adzuna title + snippet we
already have (no fetch) and returns the ids of jobs plausibly in the candidate's field,
so thematically-wrong jobs (a keyword collision like food-safety "Product Assurance"
for a software QA tester) never reach the fan-out. Validation failure -> SCREEN_INVALID.
"""

import json
from typing import Any

from jdparser.config import LLM_NODES
from jdparser.llm.client import _call
from jdparser.llm.schemas import JobScreen, ResumeProfile

_SYSTEM = """\
You are a job-relevance screener. Given a candidate profile and a list of jobs (each \
with an id, title, company, and a short description snippet), return the ids of jobs \
that are PLAUSIBLY IN THE CANDIDATE'S FIELD AND FUNCTION — the same kind of work, even \
if not a perfect fit on seniority or specific skills.

This is a COARSE filter that runs BEFORE a detailed fit evaluation, so be INCLUSIVE: \
keep anything in the candidate's field; only DROP jobs clearly in an UNRELATED industry \
or function. Example: for a software QA / manual tester, KEEP "QA Analyst", "Software \
Tester", "QA Engineer"; DROP a food-safety "Product Safety Assurance" role at a grocery \
chain, a financial "Assurance" auditor role, or a theatrical "Lighting Designer" role — \
those merely share a keyword. When genuinely unsure, KEEP the job (the later judge \
assesses real fit). Return ONLY the ids of the jobs to keep, from the ids provided.
"""


def screen_relevance(profile: ResumeProfile, jobs: list[dict[str, Any]]) -> JobScreen:
    """Return the subset of ``jobs`` (by id) plausibly in the candidate's field."""
    candidate = {
        "roles": profile.roles,
        "skills": profile.skills,
        "domains": profile.domains,
        "seniority": profile.seniority,
    }
    items = [
        {
            "id": str(job.get("id", "")),
            "title": job.get("title", ""),
            "company": (job.get("company") or {}).get("display_name", ""),
            "snippet": (job.get("description") or "")[:200],
        }
        for job in jobs
    ]
    user = json.dumps({"candidate": candidate, "jobs": items})
    return _call(LLM_NODES["screener"], _SYSTEM, user, JobScreen, "SCREEN_INVALID")
