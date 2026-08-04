"""FastAPI run service — SPEC §5 (endpoints + RunRecord §5.2), §3.10 (run-record
lifecycle), §6.2, §9.

Bridges the browser to the LangGraph pipeline: accept a resume upload, run the graph
in a background task, and expose pull-only run status/results for polling. The run
record (``data/runs/{run_id}.json``) is the single source of truth — there is no
SSE/WebSocket push channel in MVP (SPEC §9, §10 item 6).

The graph is compiled once at import (``build_graph()`` makes no network call); each run
keys the in-memory checkpointer by ``thread_id == run_id`` and throttles the fan-out to
``EVAL_FANOUT_CONCURRENCY`` (SPEC §6.1).
"""

import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.runnables import RunnableConfig

from jdparser.cache.fingerprint import compute_fingerprint
from jdparser.cache.store import get_profile, list_profiles, put_profile
from jdparser.config import (
    EVAL_FANOUT_CONCURRENCY,
    MODEL_LOGIC,
    PARSER_VERSION,
    SCHEMA_VERSION,
    SEARCH_MAX_DAYS_OLD_DEFAULT,
    UPLOADS_DIR,
    JDParserError,
    now_iso,
)
from jdparser.graph.build import build_graph
from jdparser.graph.state import JobMatchState, initial_state
from jdparser.llm.feedback import distill_notes
from jdparser.llm.resume_profiler import profile_resume
from jdparser.llm.schemas import RunRecord, StoredResumeProfile
from jdparser.llm.usage import start_run_usage
from jdparser.resume.extract_text import extract_text
from jdparser.runs.store import create_run, get_run, list_runs, update_run

# Web origin allowed by CORS (dev default matches NEXT_PUBLIC_API_BASE peer, SPEC §6.2).
WEB_ORIGIN = os.getenv("WEB_ORIGIN", "http://localhost:3000")

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[WEB_ORIGIN],
    allow_methods=["*"],
    allow_headers=["*"],
)

_graph = build_graph()  # compiled once at import (no network on compile)


def _save_upload(file: UploadFile, run_id: str) -> str:
    """Persist the uploaded resume to ``data/uploads/{run_id}.{ext}``; return the path.

    ``ext`` is the lowercased suffix of the original filename (default ``.txt`` when the
    upload carries no extension). Type validation is deferred to the graph's
    ``extract_resume_text`` (it raises ``RESUME_UNSUPPORTED_TYPE`` for non-pdf/docx/txt,
    and the run ends ``failed`` with that code in ``error``).
    """
    suffix = Path(file.filename or "").suffix.lower() or ".txt"
    path = UPLOADS_DIR / f"{run_id}{suffix}"
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as out:
        out.write(file.file.read())
    return str(path)


# Node -> human phase label for the progress display. Instant/internal nodes
# (fingerprint_resume, dedupe_jobs) are omitted; the phase just holds the prior label.
_PHASE_LABELS = {
    "extract_resume_text": "Reading your resume",
    "load_or_parse_profile": "Understanding your background",
    "plan_searches": "Planning job searches",
    "search_jobs": "Searching job boards",
    "screen_jobs": "Evaluating jobs against your resume",  # eval fan-out follows immediately
    "aggregate_matches": "Compiling your matches",
}


def _stream_progress(run_id: str, init: JobMatchState, config: RunnableConfig) -> None:
    """Run the graph via ``.stream()`` and write live progress into the run record.

    ``stream_mode="updates"`` emits one chunk per completed node — including one per job
    in the eval fan-out (verified: each ``Send`` to ``job_eval`` streams separately, not
    batched). We turn those into a phase label plus a ``jobs_done``/``jobs_total`` count
    the frontend renders as a progress bar. The final state is read afterward from the
    checkpointer by the caller; this only drives the display.
    """
    total: int | None = None
    done = 0
    for chunk in _graph.stream(init, config=config, stream_mode="updates"):
        node, update = next(iter(chunk.items()))
        # dedupe_jobs sets the full pool; screen_jobs narrows it to the capped subset that
        # actually fans out — whichever emits last is the true eval total.
        if isinstance(update, dict) and update.get("deduped_jobs") is not None:
            total = len(update["deduped_jobs"])
        if node == "job_eval":  # one update per evaluated job
            done += 1
            # ponytail: one small run-record write per job (<= SCREEN_EVAL_CAP). Fine at
            # MVP scale; debounce to every k-th job if the cap or run concurrency grows.
            update_run(run_id, jobs_done=done, jobs_total=total)
        elif node in _PHASE_LABELS:
            extra = {"jobs_total": total, "jobs_done": 0} if node == "screen_jobs" else {}
            update_run(run_id, phase=_PHASE_LABELS[node], **extra)


def _execute(
    run_id: str,
    resume_path: str = "",
    profile: dict[str, Any] | None = None,  # reason: ResumeProfile.model_dump() reused from cache/DB
    cache_key: str | None = None,           # profile's cache_key on the reuse start-mode (§4b)
    notes: list[dict[str, Any]] | None = None,  # reason: CandidateNote.model_dump() list
    locations: list[str] | None = None,     # per-run location override (None = inferred)
    broaden: bool = True,                   # False = strict locations (drop nationwide query)
    max_days_old: int | None = SEARCH_MAX_DAYS_OLD_DEFAULT,  # listing-age cap in days (0/None = any)
    include_agencies: bool = False,         # True = let recruitment-agency listings through the screen
) -> None:
    """Background task: drive the run from ``running`` to a terminal status (SPEC §3.10).

    Two start modes: from a freshly uploaded resume (``resume_path``), or from an
    already-parsed profile reused from the cache/DB (``profile``) — the latter skips the
    resume-extraction/parsing stages (no re-parse; the resume nodes pass through). On the
    reuse mode the graph never runs ``fingerprint_resume``, so ``cache_key``/``notes`` are
    seeded here (see ``jdparser.graph.state.initial_state``); on a fresh upload the graph
    derives both itself and these stay ``None``.

    On graph success the evaluated jobs are partitioned into the ``RunRecord`` audit
    buckets (``qualified_jobs`` / ``failures`` / ``rejected``) and non-fatal
    ``ErrorRecord``s flow to ``errors``. A fatal ``JDParserError`` (e.g.
    ``EVAL_COUNT_MISMATCH``) or any other exception ends the run ``failed`` — the broad
    ``except`` is mandatory so a run is never left stuck ``running``.
    """
    update_run(run_id, status="running")
    # Accounting for THIS run. A ContextVar, so concurrent background runs don't pool
    # their spend together; it reaches the fan-out worker threads too.
    usage = start_run_usage()
    init = initial_state(
        run_id,
        resume_file_path=resume_path,
        resume_profile=profile,            # pre-set -> resume stages pass through (no re-parse)
        resume_fingerprint=cache_key,       # seeded on the reuse path (fingerprint_resume short-circuits)
        candidate_notes=notes,
        search_locations=locations,        # user's location override for this run (or None)
        broaden_search=broaden,            # include the nationwide query unless strict
        max_days_old=max_days_old,         # listing-age cap in days (0/None = any age)
        include_agencies=include_agencies, # let recruitment-agency listings past the screen
    )
    config: RunnableConfig = {
        "configurable": {"thread_id": run_id},
        "max_concurrency": EVAL_FANOUT_CONCURRENCY,
    }
    try:
        _stream_progress(run_id, init, config)
        final = _graph.get_state(config).values  # final merged state from the checkpointer
        evaluated = final["evaluated_jobs"]
        qualified = final["qualified_jobs"]
        # `qualified` is is_qualified()'s STRICTER gate (SPEC §7), not just status=="qualified"
        # — a job can have status=="qualified" (subgraph-level) yet fail is_qualified()'s extra
        # checks (thematic_fit, jd_char_len, etc). `rejected` must be the complement of
        # (qualified | failures), not an independent status-based re-filter, or such a job
        # matches neither bucket and silently vanishes from every RunRecord audit list.
        qualified_ids = {e["job_id"] for e in qualified}
        failures = [e for e in evaluated if e["status"] == "failed"]
        failure_ids = {e["job_id"] for e in failures}
        rejected = [
            e for e in evaluated if e["job_id"] not in qualified_ids and e["job_id"] not in failure_ids
        ]
        update_run(
            run_id,
            status="completed",
            resume_cache_hit=final["resume_cache_hit"],
            # always available here: set by fingerprint_resume on a fresh upload, or seeded
            # above (cache_key) on the reuse path — the run's candidate identity (§4b).
            resume_cache_key=final.get("resume_fingerprint"),
            resume_profile=final.get("resume_profile"),
            qualified_jobs=qualified,
            failures=failures,
            rejected=rejected,
            errors=final["errors"],
            screened_out=final.get("screened_out", []),
            usage=usage.as_dict(),
        )
    except JDParserError as e:
        # record spend on failures too — a run that died at the judge still cost money
        update_run(run_id, status="failed", error=f"{e.code}: {e.message}", usage=usage.as_dict())
    except Exception as e:  # reason: never leave a run stuck in "running"
        update_run(run_id, status="failed", error=str(e), usage=usage.as_dict())


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _parse_locations(raw: str | None) -> list[str] | None:
    """Decode the optional `locations` form field (a JSON array of strings).

    None/empty string -> None (use the resume's inferred locations). A provided array
    (even ``[]``, meaning nationwide) -> a cleaned list overriding the inferred ones.
    """
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="locations must be a JSON array of strings")
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise HTTPException(status_code=400, detail="locations must be a JSON array of strings")
    return [s.strip() for s in value if s.strip()]


@app.post("/api/runs", status_code=202)
async def start_run(
    background: BackgroundTasks,
    file: UploadFile | None = File(None),
    profile_id: str | None = Form(None),  # cache_key of an already-parsed resume to reuse
    locations: str | None = Form(None),   # JSON array of location strings (override inferred)
    broaden: bool = Form(True),           # include broader (nationwide) results; False = strict
    max_days_old: int = Form(SEARCH_MAX_DAYS_OLD_DEFAULT),  # listing-age cap in days (0 = any age)
    include_agencies: bool = Form(False),  # let recruitment-agency listings past the screen
) -> dict[str, str]:
    run_id = str(uuid4())  # generate FIRST so the upload can be named by it
    locs = _parse_locations(locations)
    age = max(0, max_days_old)            # clamp; 0 => any age (no filter)
    if profile_id:
        # Reuse a previously parsed resume from the DB — no upload, no re-parse.
        stored = get_profile(profile_id)
        if stored is None:
            raise HTTPException(status_code=404, detail="profile not found")
        create_run(run_id=run_id, user_id="local", resume_file_path="")
        background.add_task(
            _execute, run_id, profile=stored.profile.model_dump(),
            cache_key=profile_id, notes=[n.model_dump() for n in stored.notes],
            locations=locs, broaden=broaden, max_days_old=age, include_agencies=include_agencies,
        )
    elif file is not None:
        path = _save_upload(file, run_id)  # -> data/uploads/{run_id}.{ext}
        create_run(run_id=run_id, user_id="local", resume_file_path=path)  # SPEC §8 fixed user
        background.add_task(
            _execute, run_id, resume_path=path, locations=locs, broaden=broaden,
            max_days_old=age, include_agencies=include_agencies,
        )
    else:
        raise HTTPException(status_code=400, detail="provide a file or a profile_id")
    return {"run_id": run_id, "status": "pending"}


@app.post("/api/parse")
def parse_resume_endpoint(file: UploadFile = File(...)) -> StoredResumeProfile:
    """Parse a resume into a profile WITHOUT running a job search — the profiler only.

    Fast (no Adzuna search / per-job evaluation). The result is cached, so the resume
    then appears in the reuse dropdown and a later run can search from it with no
    re-parse. A resume already parsed under the current version returns instantly.
    """
    run_id = str(uuid4())  # name the saved upload
    path = _save_upload(file, run_id)
    try:
        text = extract_text(path)
        fp = compute_fingerprint(path, text)
        existing = get_profile(fp.cache_key)
        if existing is not None:
            return existing  # already parsed under the current parser/schema version
        profile = profile_resume(text)
        rec = StoredResumeProfile(
            id=str(uuid4()),
            user_id="local",
            cache_key=fp.cache_key,
            profile=profile,
            parser_version=PARSER_VERSION,
            schema_version=SCHEMA_VERSION,
            model=MODEL_LOGIC,
            created_at=now_iso(),
            updated_at=now_iso(),
        )
        put_profile(rec)
        return rec
    except JDParserError as e:
        # resume errors (RESUME_UNSUPPORTED_TYPE / RESUME_EMPTY_TEXT) and LLM errors
        raise HTTPException(status_code=400, detail=f"{e.code}: {e.message}")


@app.get("/api/profiles")
def list_profiles_endpoint() -> list[dict[str, Any]]:  # reason: compact summaries for the picker
    """Previously parsed resumes (the cache/DB), newest first — for the reuse dropdown.

    Only profiles produced by the CURRENT parser/schema versions are offered, so a
    resume parsed with superseded logic isn't reused (it would serve stale results;
    re-uploading re-parses it under the current version).
    """
    return [
        {
            "cache_key": p.cache_key,  # used as profile_id when starting a run
            "id": p.id,
            "created_at": p.created_at,
            "updated_at": p.updated_at,
            "model": p.model,
            "seniority": p.profile.seniority,
            "roles": p.profile.roles[:3],
            "education": p.profile.education[:1],
            "locations": p.profile.locations,  # inferred preferred locations (editable pre-fill)
            # The distilled feedback list. Carried on the summary rather than behind a
            # per-profile fetch: distill_notes keeps it deliberately small, so shipping it
            # with the picker costs less than the extra round-trip and loading state.
            "notes": [n.model_dump() for n in p.notes],
        }
        for p in list_profiles()
        if p.parser_version == PARSER_VERSION and p.schema_version == SCHEMA_VERSION
    ]


@app.get("/api/runs")
def list_runs_endpoint() -> list[dict[str, Any]]:  # reason: compact run-history summaries
    """All historical runs, newest first (timestamped) — for the run-history view."""
    return [
        {
            "run_id": r.run_id,
            "status": r.status,
            "created_at": r.created_at,
            "updated_at": r.updated_at,
            "qualified_count": len(r.qualified_jobs),
            "rejected_count": len(r.rejected),
            "failed_count": len(r.failures),
            "roles": (r.resume_profile or {}).get("roles", [])[:2],
            "error": r.error,
        }
        for r in list_runs()
    ]


@app.get("/api/runs/{run_id}")
def read_run(run_id: str) -> RunRecord:
    rec = get_run(run_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="run not found")
    return rec


@app.post("/api/profiles/{cache_key}/notes")
def add_profile_note(cache_key: str, text: str = Form(...)) -> dict[str, Any]:
    """Add feedback about the CANDIDATE rather than about a specific job.

    ``POST /api/feedback`` only accepts corrections attached to a job the judge got
    wrong. Plenty of what a candidate needs to say has no job to hang it on ("I won't
    relocate", "the 2019 gap was contract work"), and before this there was nowhere to
    put it. Same distillation, same note list, no ``job_context``.

    Applies to the candidate's NEXT run; nothing already evaluated is re-judged.
    """
    stored = get_profile(cache_key)
    if stored is None:
        raise HTTPException(status_code=404, detail="profile not found")
    if not text.strip():
        raise HTTPException(status_code=400, detail="feedback text is empty")
    try:
        notes = distill_notes(stored.notes, None, text.strip())
    except JDParserError as e:
        raise HTTPException(status_code=400, detail=f"{e.code}: {e.message}")
    put_profile(stored.model_copy(update={"notes": notes, "updated_at": now_iso()}))
    return {"notes": [n.model_dump() for n in notes]}


@app.delete("/api/profiles/{cache_key}/notes/{index}")
def delete_profile_note(cache_key: str, index: int) -> dict[str, Any]:
    """Remove one note by its position in the STORED list.

    Deliberately does NOT re-distill: deletion is the user overruling the model, so
    running the list back through the LLM could reword the survivors or argue the note
    back in. Drop it and save, nothing else.

    ``index`` is the index in ``stored.notes``, NOT in whatever order the UI displays
    (the panel sorts dealbreakers first). Returns the remaining list plus the deleted
    text, so a client that somehow sent a stale index can see what actually went.
    """
    stored = get_profile(cache_key)
    if stored is None:
        raise HTTPException(status_code=404, detail="profile not found")
    if not 0 <= index < len(stored.notes):
        raise HTTPException(status_code=404, detail="note not found")
    deleted = stored.notes[index]
    remaining = [n for i, n in enumerate(stored.notes) if i != index]
    put_profile(stored.model_copy(update={"notes": remaining, "updated_at": now_iso()}))
    return {"notes": [n.model_dump() for n in remaining], "deleted": deleted.note}


@app.post("/api/feedback")
def submit_feedback(
    run_id: str = Form(...),
    job_id: str = Form(...),
    text: str = Form(...),
) -> dict[str, Any]:
    """Capture user feedback on a JUDGED job as a candidate note (keyed by the resume's
    ``cache_key``), distilled into the candidate's existing note list.

    Works in both directions, because the judge can be wrong either way:
      - a **qualified** job -> a false positive ("you said I qualify, but I don't"),
      - a **rejected** job  -> a false negative ("you passed me over, but I do fit").
    The distiller is told which via ``outcome``; a false negative usually SUPPLIES
    evidence the resume understated, rather than adding a constraint.

    ``failures`` are deliberately not eligible: a job that broke at fetch/parse has no
    judgment to disagree with. Applies to the candidate's NEXT run only; jobs already
    evaluated are never re-judged.
    """
    run = get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if run.resume_cache_key is None:
        raise HTTPException(status_code=400, detail="run has no resolvable candidate")
    stored = get_profile(run.resume_cache_key)
    if stored is None:
        raise HTTPException(status_code=404, detail="candidate profile not found")

    outcome = "qualified"
    job = next((j for j in run.qualified_jobs if j.get("job_id") == job_id), None)
    if job is None:
        outcome = "rejected"
        job = next((j for j in run.rejected if j.get("job_id") == job_id), None)
    if job is None:
        raise HTTPException(
            status_code=404, detail="job not found among this run's judged jobs"
        )
    job_context = {
        "title": job.get("title"),
        "requirements": job.get("requirements"),
        "rationale": (job.get("judgment") or {}).get("rationale"),
        "outcome": outcome,  # which way the judge went -> which correction this is
    }
    try:
        notes = distill_notes(stored.notes, job_context, text)
    except JDParserError as e:
        raise HTTPException(status_code=400, detail=f"{e.code}: {e.message}")
    updated = stored.model_copy(update={"notes": notes, "updated_at": now_iso()})
    put_profile(updated)
    return {"notes": [n.model_dump() for n in notes]}
