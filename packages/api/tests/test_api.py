"""Phase 6 API tests — SPEC §5 (endpoints + RunRecord §5.2), §3.10 (run-record
lifecycle). IMPLEMENTATION_API "Done when".

ZERO live calls: ``jdparser.server._graph`` is monkeypatched with a fake whose
``.invoke`` returns a canned final ``JobMatchState`` (or raises), so no network /
OpenRouter / Adzuna traffic occurs. ``fastapi.testclient.TestClient`` runs the
``BackgroundTask`` before the POST returns, so the run is already terminal when we GET it.

Every run writes ``data/runs/{run_id}.json`` + ``data/uploads/{run_id}.<ext>`` to the
REAL data dirs; the ``runs_cleanup`` fixture deletes exactly what each test creates so
the dirs stay at only ``.gitkeep``.
"""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from jdparser import server
from jdparser.config import RUNS_DIR, UPLOADS_DIR, JDParserError

client = TestClient(server.app)


# --- canned final state (a §3.9 spread: qualified / failed / uncertain) -------
def _evaluated_job(job_id: str, status: str) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "title": f"Engineer {job_id}",
        "company": "Acme Inc",
        "location": "Austin, TX",
        "final_url": "https://employer.example/job",
        "source": {"id": job_id},
        "jd_char_len": 800,
        "requirements": None,
        "judgment": None,
        "status": status,
        "failure_stage": "parse" if status == "failed" else None,
    }


_QUALIFIED = _evaluated_job("job-q", "qualified")
_FAILED = _evaluated_job("job-f", "failed")
_REJECTED = _evaluated_job("job-r", "uncertain")
# A non-per-job ErrorRecord (SPEC §3.10): belongs in RunRecord.errors, NOT .error.
_ADZUNA_ERROR = {
    "job_id": None,
    "stage": "adzuna_search",
    "code": "ADZUNA_HTTP",
    "message": "non-2xx from Adzuna",
    "detail": None,
}


def _final_state() -> dict[str, Any]:
    return {
        "resume_cache_hit": False,
        "resume_profile": {"roles": ["backend engineer"], "education": ["B.S. CS"]},
        "qualified_jobs": [_QUALIFIED],
        "evaluated_jobs": [_QUALIFIED, _FAILED, _REJECTED],
        "errors": [_ADZUNA_ERROR],
    }


class _FakeGraph:
    """Stands in for the compiled LangGraph: returns a canned final state or raises."""

    def __init__(self, final: dict[str, Any] | None = None, exc: Exception | None = None) -> None:
        self._final = final
        self._exc = exc

    def invoke(self, init: Any, config: Any = None) -> dict[str, Any]:
        if self._exc is not None:
            raise self._exc
        assert self._final is not None
        return self._final


# --- cleanup: delete only the run/upload files each test creates --------------
@pytest.fixture
def runs_cleanup() -> Iterator[list[str]]:
    created: list[str] = []
    yield created
    for run_id in created:
        (RUNS_DIR / f"{run_id}.json").unlink(missing_ok=True)
        for upload in UPLOADS_DIR.glob(f"{run_id}.*"):
            upload.unlink()


def _post_run() -> Any:
    return client.post(
        "/api/runs",
        files={"file": ("resume.txt", b"Senior backend engineer, 6 years Python.", "text/plain")},
    )


# --- 1. happy path: 202 -> background drives to completed, partitioned --------
def test_post_run_completes_and_partitions(
    monkeypatch: pytest.MonkeyPatch, runs_cleanup: list[str]
) -> None:
    monkeypatch.setattr(server, "_graph", _FakeGraph(final=_final_state()))

    resp = _post_run()
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "pending"
    run_id = body["run_id"]
    runs_cleanup.append(run_id)

    # the upload was saved as data/uploads/{run_id}.txt
    assert (UPLOADS_DIR / f"{run_id}.txt").exists()

    got = client.get(f"/api/runs/{run_id}")
    assert got.status_code == 200
    rec = got.json()

    assert rec["status"] == "completed"
    assert rec["resume_cache_hit"] is False
    assert rec["resume_profile"] == {"roles": ["backend engineer"], "education": ["B.S. CS"]}

    assert [j["job_id"] for j in rec["qualified_jobs"]] == ["job-q"]
    assert all(j["status"] == "qualified" for j in rec["qualified_jobs"])

    assert [j["job_id"] for j in rec["failures"]] == ["job-f"]
    assert all(j["status"] == "failed" for j in rec["failures"])

    assert [j["job_id"] for j in rec["rejected"]] == ["job-r"]
    assert all(j["status"] in ("not_qualified", "uncertain") for j in rec["rejected"])

    # the faked Adzuna error rides in `errors` (audit), never in run-level `error`
    assert rec["error"] is None
    assert any(e["stage"] == "adzuna_search" and e["code"] == "ADZUNA_HTTP" for e in rec["errors"])


# --- 2. unknown run -> 404 ----------------------------------------------------
def test_get_unknown_run_404() -> None:
    resp = client.get(f"/api/runs/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "run not found"


# --- 3. forced fatal: JDParserError -> failed, never stuck running ------------
def test_fatal_error_marks_failed_not_running(
    monkeypatch: pytest.MonkeyPatch, runs_cleanup: list[str]
) -> None:
    boom = JDParserError(code="EVAL_COUNT_MISMATCH", message="join bug")
    monkeypatch.setattr(server, "_graph", _FakeGraph(exc=boom))

    resp = _post_run()
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]
    runs_cleanup.append(run_id)

    rec = client.get(f"/api/runs/{run_id}").json()
    assert rec["status"] == "failed"
    assert rec["status"] != "running"  # never left stuck in-flight
    assert rec["error"] is not None
    assert "EVAL_COUNT_MISMATCH" in rec["error"]


# --- 4. health ----------------------------------------------------------------
def test_health() -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# --- 5. list endpoints + run-from-profile -------------------------------------
def test_list_profiles_and_runs_return_lists() -> None:
    p = client.get("/api/profiles")
    r = client.get("/api/runs")
    assert p.status_code == 200 and isinstance(p.json(), list)
    assert r.status_code == 200 and isinstance(r.json(), list)


def test_run_from_unknown_profile_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jdparser.server.get_profile", lambda key: None)
    resp = client.post("/api/runs", data={"profile_id": "does-not-exist"})
    assert resp.status_code == 404


def test_run_without_file_or_profile_400() -> None:
    resp = client.post("/api/runs")
    assert resp.status_code == 400
