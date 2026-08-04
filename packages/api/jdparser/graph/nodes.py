"""Top-level graph nodes + the deterministic display gate — SPEC §4.1, §7.

Each node takes the full ``JobMatchState`` and returns a **partial** state dict
(LangGraph merges it). Failure policy (SPEC §4.1, IMPLEMENTATION_GRAPH):

- ``extract_resume_text`` / ``fingerprint_resume`` / ``load_or_parse_profile`` /
  ``plan_searches`` are **fatal** — a ``JDParserError`` propagates out of
  ``graph.invoke`` and the API runner records the run as ``failed``.
- ``search_jobs`` is partial-tolerant — a per-query failure becomes an
  ``ErrorRecord`` and the loop continues.
- The fan-out (``evaluate_jobs`` → the subgraph) is fully fault-isolated per worker.
"""

from typing import Any
from uuid import uuid4

from langgraph.types import Send

from jdparser.cache.fingerprint import compute_fingerprint
from jdparser.cache.store import get_profile, put_profile
from jdparser.config import (
    CONFIDENCE_THRESHOLD,
    MIN_JD_CHARS,
    MODEL_LOGIC,
    PARSER_VERSION,
    SCHEMA_VERSION,
    SCREEN_EVAL_CAP,
    SEARCH_PLAN_MAX_QUERIES,
    JDParserError,
    now_iso,
)
from jdparser.graph.state import JobMatchState
from jdparser.jobs import Job
from jdparser.jobsource import SOURCE
from jdparser.jobsource.dedupe import dedupe
from jdparser.llm.resume_profiler import profile_resume
from jdparser.llm.schemas import (
    AdzunaQuery,
    ErrorRecord,
    ResumeProfile,
    StoredResumeProfile,
)
from jdparser.llm.screener import screen_relevance_batched
from jdparser.llm.search_planner import plan_queries
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
            "resume_cache_hit": True,
            "candidate_notes": [n.model_dump() for n in existing.notes],
        }
    text = state["resume_text"]
    assert text is not None
    profile = profile_resume(text)                      # llm/resume_profiler.py (gemini-3.1-flash-lite)
    rec = StoredResumeProfile(
        id=str(uuid4()),
        user_id=state["user_id"],
        cache_key=key,
        profile=profile,
        parser_version=PARSER_VERSION,
        schema_version=SCHEMA_VERSION,
        model=MODEL_LOGIC,                                # profiler slug (§6.3)
        created_at=now_iso(),
        updated_at=now_iso(),
    )
    put_profile(rec)                                    # atomic write
    return {
        "resume_profile": profile.model_dump(),
        "resume_cache_hit": False,
    }


# A `where` value Adzuna can geocode is a real place; these mean "no place" (nationwide).
_NON_GEO_LOCATIONS = frozenset(
    {"remote", "anywhere", "nationwide", "us", "usa", "united states",
     "remote (us)", "us remote", "remote us", "any"}
)


def _is_geographic(loc: str) -> bool:
    return loc.strip().lower() not in _NON_GEO_LOCATIONS


def _apply_location_override(
    plan: list[AdzunaQuery], locations: list[str], broaden: bool
) -> list[AdzunaQuery]:
    """Honor an explicit per-run location override VERBATIM.

    The planner LLM otherwise "normalizes" a user's location (e.g. narrows the whole
    state "TX" to "Austin, TX"), defeating a broad search. So when the user supplies
    locations we keep the planner's role-variety (`what`/`what_or`/`what_exclude`) but
    deterministically reassign geography: EVERY role query is scoped to the override's
    GEOGRAPHIC values (used exactly as given, cycled) — the planner's own `where`/
    nationwide choices are discarded entirely, not just the queries it happened to leave
    where-less (a prior version only reassigned already-`where`-scoped queries and left
    everything else at the planner's own nationwide default, which silently ignored the
    override for most of the plan whenever `broaden=True` — the frontend's default). With
    `distance` cleared so a state isn't shrunk to a radius. A remote/anywhere value (or
    `broaden=True`) then adds ONE nationwide (where-less) query on top, for breadth. A
    purely-remote override makes every query nationwide.
    """
    geo = [loc for loc in locations if _is_geographic(loc)]
    has_remote = any(not _is_geographic(loc) for loc in locations)
    if not geo:                                          # purely remote -> all nationwide
        return [q.model_copy(update={"where": None, "distance": None}) for q in plan]
    out = [
        q.model_copy(update={"where": geo[i % len(geo)], "distance": None})
        for i, q in enumerate(plan)
    ]
    if broaden or has_remote:                            # add one nationwide query for breadth
        out = out[: SEARCH_PLAN_MAX_QUERIES - 1] + [
            plan[0].model_copy(update={"where": None, "distance": None})
        ]
    return out[:SEARCH_PLAN_MAX_QUERIES]


def plan_searches(state: JobMatchState) -> NodeResult:
    raw_profile = state["resume_profile"]
    assert raw_profile is not None
    profile = ResumeProfile.model_validate(raw_profile)
    out: NodeResult = {}
    broaden = state.get("broaden_search", True)
    # Per-run location override: the user can expand/replace the inferred preferred
    # locations for this search. `None` = use the resume's inferred locations.
    locations = state.get("search_locations")
    if locations is not None:
        profile = profile.model_copy(update={"locations": locations})
        out["resume_profile"] = profile.model_dump()  # the judge sees the chosen locations too
    plan = plan_queries(profile)                        # llm/search_planner.py (gemini-3.1-flash-lite)
    if not plan:
        raise JDParserError(code="PLAN_EMPTY", message="planner produced no queries")
    if locations:
        # Apply the user's locations verbatim (the planner narrows states to cities otherwise).
        plan = _apply_location_override(plan, locations, broaden)
    elif not broaden:
        # Strict locations: drop the planner's nationwide (where-less) queries so the search
        # stays within the inferred locations. Guarded so it never empties the plan.
        located = [q for q in plan if q.where]
        if located:
            plan = located
    # Listing-age filter (user-controlled, deterministic): a positive value caps how old a
    # posting may be; None / <= 0 means "any age" (cleared so the planner can't re-add one).
    mdo = state.get("max_days_old")
    age = mdo if (mdo and mdo > 0) else None
    plan = [q.model_copy(update={"max_days_old": age}) for q in plan]
    out["search_plan"] = [q.model_dump() for q in plan]
    return out


def search_jobs(state: JobMatchState) -> NodeResult:
    raw_plan = state["search_plan"]
    assert raw_plan is not None
    queries = [AdzunaQuery.model_validate(q) for q in raw_plan]
    results: list[Job] = []
    errors: list[dict[str, Any]] = []   # reason: ErrorRecord payloads (SPEC §3.10)
    for q in queries:                                   # continue-on-partial-failure (policy lives here)
        try:
            results.extend(SOURCE.search([q]))           # jobsource/adzuna
        except JDParserError as e:
            errors.append(
                ErrorRecord(
                    job_id=None, stage="job_search", code=e.code, message=e.message
                ).model_dump()
            )
    return {"job_results": [j.model_dump() for j in results], "errors": errors}


def dedupe_jobs(state: JobMatchState) -> NodeResult:
    jobs = [Job.model_validate(j) for j in state["job_results"]]
    return {"deduped_jobs": [j.model_dump() for j in dedupe(jobs)]}   # jobsource/dedupe.py


def _screened_out_entry(j: Job, reason: str) -> dict[str, Any]:
    return {
        "job_id": j.id,
        "title": j.title,
        "company": j.company,
        "location": j.location,
        # "off_field" | "agency" (recruitment agency, not opted in) | "over_cap" (past budget)
        "reason": reason,
    }


def screen_jobs(state: JobMatchState) -> NodeResult:
    """Bounded relevance funnel BEFORE the expensive fan-out (SPEC §4.1 + cost guard).

    Three jobs in one, all on the cheap search-result title/company/snippet (no fetch):
    (1) a coarse same-field filter dropping thematically-wrong jobs (keyword collisions
    like a food-safety "Product Assurance" role for a software QA tester); (2) a
    RECRUITMENT-AGENCY filter — agency postings are a second relevance dimension, NOT
    relevant by default, so they are screened out (reason ``agency``) unless the run set
    ``include_agencies``; (3) a hard ``SCREEN_EVAL_CAP`` ceiling so a wide pull can't blow
    past the frontend poll timeout. Survivors are RANKED most-relevant-first (agencies, when
    included, sink below direct employers so the budget fills with direct employers first).

    Fault-tolerant: a screen failure, or a screen that would drop *everything*, falls back
    to keeping the deduped pool — but the cap is ALWAYS applied as a final ceiling."""
    raw_jobs = state["deduped_jobs"]
    raw_profile = state["resume_profile"]
    if not raw_jobs or raw_profile is None:
        return {}
    jobs = [Job.model_validate(j) for j in raw_jobs]
    profile = ResumeProfile.model_validate(raw_profile)
    include_agencies = state.get("include_agencies", False)
    errors: list[dict[str, Any]] = []
    agency_ids: set[str] = set()
    try:
        result = screen_relevance_batched(profile, jobs)       # llm/screener.py (batched, Gemini Flash Lite)
        ranked_ids = result.relevant_job_ids                   # most-relevant-first (screener contract)
        agency_ids = set(result.agency_job_ids)
    except JDParserError as e:
        # never let the screen crash a run — fall back to the full pool (still capped below)
        errors.append(ErrorRecord(job_id=None, stage="screen", code=e.code, message=e.message).model_dump())
        ranked_ids = []

    by_id = {j.id: j for j in jobs}
    # in-field jobs in the screener's relevance order, de-duped, then everything it dropped
    kept_relevant = [by_id[i] for i in dict.fromkeys(ranked_ids) if i in by_id]
    relevant = {j.id for j in kept_relevant}
    off_field = [j for j in jobs if j.id not in relevant]

    agency_excluded: list[Job] = []
    if kept_relevant:
        # Agency = a second relevance dimension: deprioritize to the tail (direct employers
        # fill the eval budget first); drop entirely unless the run opted in.
        directs = [j for j in kept_relevant if j.id not in agency_ids]
        agencies = [j for j in kept_relevant if j.id in agency_ids]
        ordered = directs + agencies if include_agencies else directs
        if not include_agencies:
            agency_excluded = agencies
        dropped_off_field = off_field
    else:
        # Fallback: screen failed / dropped everything -> keep the deduped pool as-is.
        ordered = jobs
        dropped_off_field = []

    kept = ordered[:SCREEN_EVAL_CAP]
    over_cap = ordered[SCREEN_EVAL_CAP:]
    # Tag kept agencies so the "Agency" badge rides through to the EvaluatedJob.
    kept = [
        j.model_copy(update={"is_recruitment_agency": True}) if j.id in agency_ids else j
        for j in kept
    ]
    screened_out = (
        [_screened_out_entry(j, "off_field") for j in dropped_off_field]
        + [_screened_out_entry(j, "agency") for j in agency_excluded]
        + [_screened_out_entry(j, "over_cap") for j in over_cap]
    )
    out: NodeResult = {"deduped_jobs": [j.model_dump() for j in kept]}
    if screened_out:
        out["screened_out"] = screened_out
    if errors:
        out["errors"] = errors
    return out


def evaluate_jobs(state: JobMatchState) -> list[Send]:          # conditional edge fn, fan-out
    # inject the profile into each worker payload (SPEC §3.2, §4.1)
    return [
        Send(
            "job_eval",
            {
                "job": j,
                "profile": state["resume_profile"],
                "notes": state.get("candidate_notes", []),
                "result": [],
            },
        )
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
        and j["thematic_fit"]
        and not j["failed_dealbreakers"]
        and not j["missing_hard_requirements"]
        and ej.get("requirements") is not None        # §7.1: full JD parsed
        and ej.get("final_url")                        # §7.1: resolved URL
        and (ej.get("jd_char_len") or 0) >= MIN_JD_CHARS  # §7.1: JD length gate
    )
