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
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from builders import profile, stored_profile
from jdparser import server
from jdparser.config import RUNS_DIR, UPLOADS_DIR, JDParserError
from jdparser.llm.schemas import CandidateNote, ResumeProfile, StoredResumeProfile
from jdparser.runs.store import create_run, get_run, list_runs, update_run

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
        "resume_fingerprint": "fake-cache-key",  # set by fingerprint_resume on a real run
        "resume_profile": {"roles": ["backend engineer"], "education": ["B.S. CS"]},
        "qualified_jobs": [_QUALIFIED],
        "evaluated_jobs": [_QUALIFIED, _FAILED, _REJECTED],
        "errors": [_ADZUNA_ERROR],
        "screened_out": [{"job_id": "x", "title": "Grocery QA", "company": "Mart", "location": "TX"}],
    }


class _FakeGraph:
    """Stands in for the compiled LangGraph: streams canned progress updates then
    exposes a canned final state via ``get_state``, or raises during the stream."""

    def __init__(self, final: dict[str, Any] | None = None, exc: Exception | None = None) -> None:
        self._final = final
        self._exc = exc

    def stream(self, init: Any, config: Any = None, stream_mode: Any = None) -> Any:
        if self._exc is not None:
            raise self._exc
        assert self._final is not None
        evaluated = self._final["evaluated_jobs"]
        # one screen_jobs update (sets the total) then one job_eval update per job
        yield {"screen_jobs": {"deduped_jobs": evaluated}}
        for job in evaluated:
            yield {"job_eval": {"evaluated_jobs": [job]}}

    def get_state(self, config: Any = None) -> Any:
        assert self._final is not None
        return SimpleNamespace(values=self._final)


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
    assert rec["resume_cache_key"] == "fake-cache-key"  # from final state's resume_fingerprint (§4b)
    assert rec["resume_profile"] == {"roles": ["backend engineer"], "education": ["B.S. CS"]}
    assert [s["title"] for s in rec["screened_out"]] == ["Grocery QA"]

    assert [j["job_id"] for j in rec["qualified_jobs"]] == ["job-q"]
    assert all(j["status"] == "qualified" for j in rec["qualified_jobs"])

    assert [j["job_id"] for j in rec["failures"]] == ["job-f"]
    assert all(j["status"] == "failed" for j in rec["failures"])

    assert [j["job_id"] for j in rec["rejected"]] == ["job-r"]
    assert all(j["status"] in ("not_qualified", "uncertain") for j in rec["rejected"])

    # the faked Adzuna error rides in `errors` (audit), never in run-level `error`
    assert rec["error"] is None
    assert any(e["stage"] == "adzuna_search" and e["code"] == "ADZUNA_HTTP" for e in rec["errors"])


# --- 1b. a job whose subgraph status=="qualified" but fails the STRICTER
#     is_qualified() gate (SPEC §7 — e.g. thematic_fit=False) must land in `rejected`,
#     not vanish from every bucket (the gap this test guards against). ------------
def test_status_qualified_but_gated_lands_in_rejected(
    monkeypatch: pytest.MonkeyPatch, runs_cleanup: list[str]
) -> None:
    gated = _evaluated_job("job-q2", "qualified")  # status=="qualified", excluded from qualified_jobs below
    final = _final_state()
    final["evaluated_jobs"] = [*final["evaluated_jobs"], gated]  # qualified_jobs deliberately NOT updated
    monkeypatch.setattr(server, "_graph", _FakeGraph(final=final))

    resp = _post_run()
    run_id = resp.json()["run_id"]
    runs_cleanup.append(run_id)

    rec = client.get(f"/api/runs/{run_id}").json()
    assert [j["job_id"] for j in rec["qualified_jobs"]] == ["job-q"]  # unchanged
    assert [j["job_id"] for j in rec["failures"]] == ["job-f"]        # unchanged
    assert set(j["job_id"] for j in rec["rejected"]) == {"job-r", "job-q2"}  # job-q2 no longer lost


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


def test_legacy_adzuna_search_stage_still_loads(runs_cleanup: list[str]) -> None:
    """A run record written BEFORE the job-source seam must still be readable.

    The search stage was renamed "adzuna_search" -> "job_search" (REFACTOR_AUDIT Phase 3),
    but the old value is persisted in existing data/runs/*.json. list_runs skips any record
    that fails validation with a bare `except: continue` — so dropping the old literal from
    ErrorStage would silently erase the user's run history: no error, no log, no failing
    test anywhere else. This is that test.
    """
    run_id = str(uuid.uuid4())
    runs_cleanup.append(run_id)
    rec = create_run(run_id=run_id, user_id="local", resume_file_path="")
    update_run(
        run_id,
        status="completed",
        errors=[{"job_id": None, "stage": "adzuna_search", "code": "ADZUNA_HTTP",
                 "message": "500", "detail": None}],
    )
    assert rec.run_id == run_id

    reloaded = get_run(run_id)
    assert reloaded is not None
    assert reloaded.errors[0]["stage"] == "adzuna_search"
    assert run_id in {r.run_id for r in list_runs()}   # not swallowed by the listing


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


def test_parse_only_returns_profile_without_search(monkeypatch: pytest.MonkeyPatch) -> None:
    from jdparser.llm.schemas import Fingerprint, ResumeProfile

    prof = profile()
    called = {"n": 0}

    def _prof(_text: str) -> ResumeProfile:
        called["n"] += 1
        return prof

    monkeypatch.setattr("jdparser.server._save_upload", lambda f, rid: "ignored.txt")
    monkeypatch.setattr("jdparser.server.extract_text", lambda p: "x" * 500)
    monkeypatch.setattr(
        "jdparser.server.compute_fingerprint",
        lambda p, t: Fingerprint(cache_key="parse-test"),
    )
    monkeypatch.setattr("jdparser.server.get_profile", lambda key: None)  # cache miss -> parse
    monkeypatch.setattr("jdparser.server.profile_resume", _prof)
    monkeypatch.setattr("jdparser.server.put_profile", lambda rec: None)

    resp = client.post("/api/parse", files={"file": ("r.txt", b"hello", "text/plain")})
    assert resp.status_code == 200
    body = resp.json()
    assert body["profile"]["roles"] == ["backend engineer"]
    assert body["cache_key"] == "parse-test"
    assert called["n"] == 1  # profiler ran once; no graph/search was invoked


# --- 6. POST /api/feedback -----------------------------------------------------
def _stored_profile(cache_key: str, notes: list[CandidateNote] | None = None) -> StoredResumeProfile:
    return stored_profile(cache_key=cache_key, notes=notes or [])


def _post_completed_run(monkeypatch: pytest.MonkeyPatch, runs_cleanup: list[str]) -> str:
    monkeypatch.setattr(server, "_graph", _FakeGraph(final=_final_state()))
    resp = _post_run()
    run_id = resp.json()["run_id"]
    runs_cleanup.append(run_id)
    return run_id


def test_feedback_happy_path_distills_and_saves_notes(
    monkeypatch: pytest.MonkeyPatch, runs_cleanup: list[str]
) -> None:
    run_id = _post_completed_run(monkeypatch, runs_cleanup)  # resume_cache_key == "fake-cache-key"

    stored = _stored_profile("fake-cache-key")
    monkeypatch.setattr(server, "get_profile", lambda key: stored if key == "fake-cache-key" else None)
    canned = [CandidateNote(note="No active clearance", kind="dealbreaker", source="feedback on 'Engineer job-q'")]
    monkeypatch.setattr(server, "distill_notes", lambda existing, ctx, text: canned)
    saved: dict[str, Any] = {}
    monkeypatch.setattr(server, "put_profile", lambda rec: saved.__setitem__("record", rec))

    resp = client.post(
        "/api/feedback",
        data={"run_id": run_id, "job_id": "job-q", "text": "I don't have an active clearance"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"notes": [n.model_dump() for n in canned]}
    assert saved["record"].cache_key == "fake-cache-key"
    assert saved["record"].notes == canned


def test_feedback_unknown_run_404() -> None:
    resp = client.post(
        "/api/feedback", data={"run_id": str(uuid.uuid4()), "job_id": "job-q", "text": "x"}
    )
    assert resp.status_code == 404


def test_feedback_run_without_cache_key_400(runs_cleanup: list[str]) -> None:
    # a run predating this feature (or otherwise unresolvable) has no candidate identity
    rec = create_run(run_id=str(uuid.uuid4()), user_id="local", resume_file_path="")
    runs_cleanup.append(rec.run_id)
    resp = client.post("/api/feedback", data={"run_id": rec.run_id, "job_id": "job-q", "text": "x"})
    assert resp.status_code == 400


def test_feedback_on_rejected_job_records_a_false_negative(
    monkeypatch: pytest.MonkeyPatch, runs_cleanup: list[str]
) -> None:
    """The judge can be wrong in the candidate's favour too.

    "You passed me over but I do fit" is as real a correction as "you said I qualify but
    I don't", and it is the ONLY way a candidate can supply evidence their resume
    understated. `outcome` tells the distiller which direction it is recording — without
    it, a false-negative correction reads exactly like a false-positive one and the
    distiller would write a dealbreaker that makes future matching strictly worse.
    """
    run_id = _post_completed_run(monkeypatch, runs_cleanup)
    monkeypatch.setattr(server, "get_profile", lambda key: _stored_profile("fake-cache-key"))
    monkeypatch.setattr(server, "put_profile", lambda rec: None)

    seen: dict[str, Any] = {}

    def _distill(existing: Any, ctx: dict[str, Any], text: str) -> list[CandidateNote]:
        seen["ctx"] = ctx
        return [CandidateNote(note="Has production Kubernetes experience", kind="context", source="s")]

    monkeypatch.setattr(server, "distill_notes", _distill)

    # job-r is in `rejected` (status "uncertain" -> excluded from qualified_jobs)
    resp = client.post(
        "/api/feedback",
        data={"run_id": run_id, "job_id": "job-r", "text": "I ran K8s in prod at Acme"},
    )
    assert resp.status_code == 200
    assert seen["ctx"]["outcome"] == "rejected"
    assert resp.json()["notes"][0]["kind"] == "context"


def test_feedback_on_qualified_job_is_marked_as_such(
    monkeypatch: pytest.MonkeyPatch, runs_cleanup: list[str]
) -> None:
    """The pre-existing direction still reports itself correctly."""
    run_id = _post_completed_run(monkeypatch, runs_cleanup)
    monkeypatch.setattr(server, "get_profile", lambda key: _stored_profile("fake-cache-key"))
    monkeypatch.setattr(server, "put_profile", lambda rec: None)

    seen: dict[str, Any] = {}

    def _distill(existing: Any, ctx: dict[str, Any], text: str) -> list[CandidateNote]:
        seen["ctx"] = ctx
        return []

    monkeypatch.setattr(server, "distill_notes", _distill)
    resp = client.post(
        "/api/feedback", data={"run_id": run_id, "job_id": "job-q", "text": "no clearance"}
    )
    assert resp.status_code == 200
    assert seen["ctx"]["outcome"] == "qualified"


def test_feedback_job_not_among_qualified_404(
    monkeypatch: pytest.MonkeyPatch, runs_cleanup: list[str]
) -> None:
    run_id = _post_completed_run(monkeypatch, runs_cleanup)
    monkeypatch.setattr(server, "get_profile", lambda key: _stored_profile("fake-cache-key"))

    # job-f is a FAILED job — it broke before/at evaluation, so there is no judgment to
    # disagree with. Still out of scope now that `rejected` is eligible.
    resp = client.post(
        "/api/feedback", data={"run_id": run_id, "job_id": "job-f", "text": "not a fit"}
    )
    assert resp.status_code == 404
