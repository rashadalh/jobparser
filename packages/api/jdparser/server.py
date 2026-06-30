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
from jdparser.graph.state import JobMatchState
from jdparser.llm.resume_profiler import profile_resume
from jdparser.llm.schemas import RunRecord, StoredResumeProfile
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


def _execute(
    run_id: str,
    resume_path: str = "",
    profile: dict[str, Any] | None = None,  # reason: ResumeProfile.model_dump() reused from cache/DB
    profile_id: str | None = None,
    locations: list[str] | None = None,     # per-run location override (None = inferred)
    broaden: bool = True,                   # False = strict locations (drop nationwide query)
    max_days_old: int | None = SEARCH_MAX_DAYS_OLD_DEFAULT,  # listing-age cap in days (0/None = any)
) -> None:
    """Background task: drive the run from ``running`` to a terminal status (SPEC §3.10).

    Two start modes: from a freshly uploaded resume (``resume_path``), or from an
    already-parsed profile reused from the cache/DB (``profile``) — the latter skips the
    resume-extraction/parsing stages (no re-parse; the resume nodes pass through).

    On graph success the evaluated jobs are partitioned into the ``RunRecord`` audit
    buckets (``qualified_jobs`` / ``failures`` / ``rejected``) and non-fatal
    ``ErrorRecord``s flow to ``errors``. A fatal ``JDParserError`` (e.g.
    ``EVAL_COUNT_MISMATCH``) or any other exception ends the run ``failed`` — the broad
    ``except`` is mandatory so a run is never left stuck ``running``.
    """
    update_run(run_id, status="running")
    init: JobMatchState = {
        "run_id": run_id,
        "user_id": "local",
        "resume_file_path": resume_path,
        "resume_text": None,
        "resume_fingerprint": None,
        "resume_profile_id": profile_id,
        "resume_profile": profile,            # pre-set -> resume stages pass through (no re-parse)
        "resume_cache_hit": profile is not None,
        "search_locations": locations,        # user's location override for this run (or None)
        "broaden_search": broaden,            # include the nationwide query unless strict
        "max_days_old": max_days_old,         # listing-age cap in days (0/None = any age)
        "search_plan": None,
        "adzuna_results": [],
        "deduped_jobs": [],
        "screened_out": [],
        "evaluated_jobs": [],
        "qualified_jobs": [],
        "errors": [],
    }
    config: RunnableConfig = {
        "configurable": {"thread_id": run_id},
        "max_concurrency": EVAL_FANOUT_CONCURRENCY,
    }
    try:
        final = _graph.invoke(init, config=config)
        evaluated = final["evaluated_jobs"]
        update_run(
            run_id,
            status="completed",
            resume_cache_hit=final["resume_cache_hit"],
            resume_profile=final.get("resume_profile"),
            qualified_jobs=final["qualified_jobs"],
            failures=[e for e in evaluated if e["status"] == "failed"],
            rejected=[e for e in evaluated if e["status"] in ("not_qualified", "uncertain")],
            errors=final["errors"],
            screened_out=final.get("screened_out", []),
        )
    except JDParserError as e:
        update_run(run_id, status="failed", error=f"{e.code}: {e.message}")
    except Exception as e:  # reason: never leave a run stuck in "running"
        update_run(run_id, status="failed", error=str(e))


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
            _execute, run_id, profile=stored.profile.model_dump(), profile_id=stored.id,
            locations=locs, broaden=broaden, max_days_old=age,
        )
    elif file is not None:
        path = _save_upload(file, run_id)  # -> data/uploads/{run_id}.{ext}
        create_run(run_id=run_id, user_id="local", resume_file_path=path)  # SPEC §8 fixed user
        background.add_task(
            _execute, run_id, resume_path=path, locations=locs, broaden=broaden, max_days_old=age,
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
            file_hash=fp.file_hash,
            text_hash=fp.text_hash,
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
