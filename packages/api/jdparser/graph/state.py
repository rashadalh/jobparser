"""LangGraph state channels — SPEC §3.1 (``JobMatchState``), §3.2 (``JobEvalState``).

LangGraph state channels are ``TypedDict``s carrying ``.model_dump()`` payloads
(never live Pydantic objects). The fan-out reducer channels (``evaluated_jobs``,
``errors``) use ``operator.add`` so concurrent workers **append** (never overwrite);
see SPEC §3.10 for the lifecycle (the #1 hazard).

``JobEvalState`` declares the shared ``evaluated_jobs`` / ``errors`` reducer channels
**in addition to** its per-job fields. This is required: LangGraph only propagates a
subgraph's returned keys to the parent reducers if the subgraph's own state schema
also declares those channels (verified against langgraph 0.6.11) — otherwise
``finalize`` / ``record_failure`` emissions are silently dropped and
``aggregate_matches`` raises ``EVAL_COUNT_MISMATCH``.
"""

import operator
from typing import Annotated, Any, TypedDict

# reason: heterogeneous LangGraph state payloads (SPEC §3.1/§3.2)
_Json = dict[str, Any]


class JobMatchState(TypedDict):
    # --- run identity / inputs ---
    run_id: str
    user_id: str
    resume_file_path: str

    # --- resume → profile ---
    resume_text: str | None
    resume_fingerprint: str | None        # == cache_key (§3.7)
    resume_profile_id: str | None
    resume_profile: _Json | None          # ResumeProfile.model_dump()
    resume_cache_hit: bool

    # --- discovery ---
    search_locations: list[str] | None    # per-run location override (None = use profile's inferred)
    search_plan: list[_Json] | None       # list[AdzunaQuery.model_dump()]
    adzuna_results: list[_Json]           # raw Adzuna job dicts (§3.8.1)
    deduped_jobs: list[_Json]             # deduped raw Adzuna job dicts

    # --- evaluation (fan-out reducers; see §3.10 for lifecycle) ---
    evaluated_jobs: Annotated[list[_Json], operator.add]   # list[EvaluatedJob]
    qualified_jobs: list[_Json]           # filtered subset of evaluated_jobs
    errors: Annotated[list[_Json], operator.add]           # list[ErrorRecord]


class JobEvalState(TypedDict):
    job: _Json                  # one deduped Adzuna job dict (§3.8.1)
    profile: _Json              # ResumeProfile.model_dump() injected via Send (§3.2)
    final_url: str | None
    fetched: _Json | None       # FetchResult (§3.8.2)
    jd_text: str | None
    jd_quality: _Json | None    # QualityResult (§3.8.3)
    requirements: _Json | None  # JobRequirements.model_dump()
    judgment: _Json | None      # FitJudgment.model_dump()
    result: list[_Json]         # in-subgraph failure marker (§3.2)
    # --- shared reducer channels: REQUIRED so finalize/record_failure emit into the
    #     PARENT evaluated_jobs/errors reducers (SPEC §3.10). Mechanism detail. ---
    evaluated_jobs: Annotated[list[_Json], operator.add]
    errors: Annotated[list[_Json], operator.add]
