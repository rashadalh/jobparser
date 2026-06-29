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

import os
from pathlib import Path
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.runnables import RunnableConfig

from jdparser.config import EVAL_FANOUT_CONCURRENCY, UPLOADS_DIR, JDParserError
from jdparser.graph.build import build_graph
from jdparser.graph.state import JobMatchState
from jdparser.llm.schemas import RunRecord
from jdparser.runs.store import create_run, get_run, update_run

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


def _execute(run_id: str, resume_path: str) -> None:
    """Background task: drive the run from ``running`` to a terminal status (SPEC §3.10).

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
        "resume_profile_id": None,
        "resume_profile": None,
        "resume_cache_hit": False,
        "search_plan": None,
        "adzuna_results": [],
        "deduped_jobs": [],
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
        )
    except JDParserError as e:
        update_run(run_id, status="failed", error=f"{e.code}: {e.message}")
    except Exception as e:  # reason: never leave a run stuck in "running"
        update_run(run_id, status="failed", error=str(e))


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/runs", status_code=202)
async def start_run(
    background: BackgroundTasks,
    file: UploadFile = File(...),
) -> dict[str, str]:
    run_id = str(uuid4())  # generate FIRST so the upload can be named by it
    path = _save_upload(file, run_id)  # -> data/uploads/{run_id}.{ext}
    create_run(run_id=run_id, user_id="local", resume_file_path=path)  # SPEC §8 fixed user
    background.add_task(_execute, run_id, path)
    return {"run_id": run_id, "status": "pending"}


@app.get("/api/runs/{run_id}")
def read_run(run_id: str) -> RunRecord:
    rec = get_run(run_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="run not found")
    return rec
