"""Top-level graph nodes + the deterministic display gate — SPEC §4.1, §7.

Each node takes the full ``JobMatchState`` and returns a **partial** state dict
(LangGraph merges it). Failure policy (SPEC §4.1, IMPLEMENTATION_GRAPH):

- ``extract_resume_text`` / ``fingerprint_resume`` / ``load_or_parse_profile`` /
  ``plan_searches`` are **fatal** — a ``JDParserError`` propagates out of
  ``graph.invoke`` and the API runner records the run as ``failed``.
- ``run_adzuna_search`` is partial-tolerant — a per-query failure becomes an
  ``ErrorRecord`` and the loop continues.
- The fan-out (``evaluate_jobs`` → the subgraph) is fully fault-isolated per worker.
"""

from typing import Any
from uuid import uuid4

from langgraph.types import Send

from jdparser.adzuna.client import run_search_plan
from jdparser.adzuna.dedupe import dedupe
from jdparser.cache.fingerprint import compute_fingerprint
from jdparser.cache.store import get_profile, put_profile
from jdparser.config import (
    CONFIDENCE_THRESHOLD,
    MIN_JD_CHARS,
    MODEL_GLM,
    PARSER_VERSION,
    SCHEMA_VERSION,
    JDParserError,
    now_iso,
)
from jdparser.graph.state import JobMatchState
from jdparser.llm.resume_profiler import profile_resume
from jdparser.llm.schemas import (
    AdzunaQuery,
    ErrorRecord,
    ResumeProfile,
    StoredResumeProfile,
)
from jdparser.llm.screener import screen_relevance
from jdparser.llm.search_planner import plan_adzuna_queries
from jdparser.resume.extract_text import extract_text

# reason: heterogeneous LangGraph state payloads (SPEC §3.1/§3.2)
NodeResult = dict[str, Any]


def extract_resume_text(state: JobMatchState) -> NodeResult:
    # Run started from an already-parsed profile (reused from the cache/DB) — skip the
    # resume-extraction/parsing stages entirely (no re-parse). See server `_execute`.
    if state.get("resume_profile") is not None:
        return {}
    text = extract_text(state["resume_file_path"])     # resume/extract_text.py (fatal on failure)
    return {"resume_text": text}


def fingerprint_resume(state: JobMatchState) -> NodeResult:
    if state.get("resume_profile") is not None:
        return {}                                      # pre-loaded profile: nothing to fingerprint
    text = state["resume_text"]
    assert text is not None  # set by extract_resume_text (prior node)
    fp = compute_fingerprint(state["resume_file_path"], text)
    return {"resume_fingerprint": fp.cache_key}        # only cache_key is stored in state


def load_or_parse_profile(state: JobMatchState) -> NodeResult:
    if state.get("resume_profile") is not None:
        return {"resume_cache_hit": True}              # pre-loaded profile: reuse, never re-parse
    key = state["resume_fingerprint"]
    assert key is not None  # set by fingerprint_resume (prior node)
    existing = get_profile(key)                         # cache/store.py
    if existing is not None:
        return {
            "resume_profile": existing.profile.model_dump(),
            "resume_profile_id": existing.id,
            "resume_cache_hit": True,
        }
    text = state["resume_text"]
    assert text is not None
    profile = profile_resume(text)                      # llm/resume_profiler.py (GLM 5.2)
    # recompute the full fingerprint here on miss (state only carries cache_key)
    fp = compute_fingerprint(state["resume_file_path"], text)
    rec = StoredResumeProfile(
        id=str(uuid4()),
        user_id=state["user_id"],
        file_hash=fp.file_hash,
        text_hash=fp.text_hash,
        cache_key=key,
        profile=profile,
        parser_version=PARSER_VERSION,
        schema_version=SCHEMA_VERSION,
        model=MODEL_GLM,                                # profiler slug (§6.3)
        created_at=now_iso(),
        updated_at=now_iso(),
    )
    put_profile(rec)                                    # atomic write
    return {
        "resume_profile": profile.model_dump(),
        "resume_profile_id": rec.id,
        "resume_cache_hit": False,
    }


def plan_searches(state: JobMatchState) -> NodeResult:
    raw_profile = state["resume_profile"]
    assert raw_profile is not None
    profile = ResumeProfile.model_validate(raw_profile)
    out: NodeResult = {}
    # Per-run location override: the user can expand/replace the inferred preferred
    # locations for this search. `None` = use the resume's inferred locations.
    locations = state.get("search_locations")
    if locations is not None:
        profile = profile.model_copy(update={"locations": locations})
        out["resume_profile"] = profile.model_dump()  # the judge sees the chosen locations too
    plan = plan_adzuna_queries(profile)                 # llm/search_planner.py (GLM 5.2)
    if not plan:
        raise JDParserError(code="PLAN_EMPTY", message="planner produced no queries")
    # Strict locations: drop the planner's nationwide (where-less) queries so the search
    # stays within the chosen locations. Guarded so it never empties the plan.
    if not state.get("broaden_search", True):
        located = [q for q in plan if q.where]
        if located:
            plan = located
    out["search_plan"] = [q.model_dump() for q in plan]
    return out


def run_adzuna_search(state: JobMatchState) -> NodeResult:
    raw_plan = state["search_plan"]
    assert raw_plan is not None
    queries = [AdzunaQuery.model_validate(q) for q in raw_plan]
    results: list[dict[str, Any]] = []  # reason: heterogeneous Adzuna JSON (SPEC §3.8.1)
    errors: list[dict[str, Any]] = []   # reason: ErrorRecord payloads (SPEC §3.10)
    for q in queries:                                   # continue-on-partial-failure (policy lives here)
        try:
            results.extend(run_search_plan([q]))        # adzuna/client.py
        except JDParserError as e:
            errors.append(
                ErrorRecord(
                    job_id=None, stage="adzuna_search", code=e.code, message=e.message
                ).model_dump()
            )
    return {"adzuna_results": results, "errors": errors}


def dedupe_jobs(state: JobMatchState) -> NodeResult:
    return {"deduped_jobs": dedupe(state["adzuna_results"])}    # adzuna/dedupe.py


def screen_jobs(state: JobMatchState) -> NodeResult:
    """Coarse same-field relevance filter (Adzuna title+snippet) BEFORE the expensive
    fan-out — drops thematically-wrong jobs (keyword collisions). Fault-tolerant: a
    screen failure, or a screen that would drop *everything*, leaves the pool intact."""
    jobs = state["deduped_jobs"]
    raw_profile = state["resume_profile"]
    if not jobs or raw_profile is None:
        return {}
    profile = ResumeProfile.model_validate(raw_profile)
    try:
        result = screen_relevance(profile, jobs)               # llm/screener.py (Gemini Flash Lite)
    except JDParserError as e:
        # never let the screen crash a run — keep all jobs, record the error
        err = ErrorRecord(job_id=None, stage="screen", code=e.code, message=e.message)
        return {"errors": [err.model_dump()]}
    relevant = set(result.relevant_job_ids)
    kept = [j for j in jobs if str(j.get("id")) in relevant]
    if not kept:                                               # dropped everything / junk ids -> keep all
        return {}
    dropped = [j for j in jobs if str(j.get("id")) not in relevant]
    screened_out = [
        {
            "job_id": str(j.get("id", "")),
            "title": j.get("title", ""),
            "company": (j.get("company") or {}).get("display_name", ""),
            "location": (j.get("location") or {}).get("display_name", ""),
        }
        for j in dropped
    ]
    return {"deduped_jobs": kept, "screened_out": screened_out}


def evaluate_jobs(state: JobMatchState) -> list[Send]:          # conditional edge fn, fan-out
    # inject the profile into each worker payload (SPEC §3.2, §4.1)
    return [
        Send("job_eval", {"job": j, "profile": state["resume_profile"], "result": []})
        for j in state["deduped_jobs"]
    ]


def aggregate_matches(state: JobMatchState) -> NodeResult:
    evaluated = state["evaluated_jobs"]
    if len(evaluated) != len(state["deduped_jobs"]):
        # join bug -> fatal: propagates out of invoke; API marks run failed (SPEC §3.10)
        raise JDParserError(
            code="EVAL_COUNT_MISMATCH",
            message=f"{len(evaluated)} != {len(state['deduped_jobs'])}",
        )
    qualified = [ej for ej in evaluated if is_qualified(ej)]    # SPEC §7
    return {"qualified_jobs": qualified}


# --- §7: deterministic display gate (copied verbatim from SPEC §7; the bare `dict`
#     param is rendered as `dict[str, Any]` per the spec's bare-dict rendering rule) -
def is_qualified(ej: dict[str, Any]) -> bool:  # reason: EvaluatedJob.model_dump() payload (SPEC §3.9)
    j = ej.get("judgment")
    return bool(
        ej["status"] == "qualified"
        and j is not None
        and j["decision"] == "qualified"
        and j["confidence"] >= CONFIDENCE_THRESHOLD
        and not j["failed_dealbreakers"]
        and not j["missing_hard_requirements"]
        and ej.get("requirements") is not None        # §7.1: full JD parsed
        and ej.get("final_url")                        # §7.1: resolved URL
        and (ej.get("jd_char_len") or 0) >= MIN_JD_CHARS  # §7.1: JD length gate
    )
