"""Per-job evaluation subgraph — SPEC §4.3, §3.9.

Stages run in order ``resolve_url → fetch_page → extract_jd → check_jd →
parse_requirements → judge_fit → finalize``. Each non-terminal stage tries its work
and, on a handled failure, sets ``s["result"] = [<failure marker>]``; a per-stage
router sees the non-empty ``result`` and routes to ``record_failure`` instead of the
next stage. ``finalize`` (success) and ``record_failure`` (failure) are the two
terminal nodes; each emits one clean ``EvaluatedJob`` into the parent
``evaluated_jobs`` reducer (SPEC §3.10). ``result`` itself is never emitted to the
parent.

Subgraph node name → ``failure_stage`` mapping (SPEC §4.3 table; node names
intentionally differ from the ``FailureStage`` enum values):

| node ``resolve_url`` | stage ``resolve`` | | node ``fetch_page`` | stage ``fetch`` |
| node ``extract_jd`` | stage ``extract`` | | node ``check_jd`` | stage ``quality`` |
| node ``parse_requirements`` | stage ``parse`` | | node ``judge_fit`` | stage ``judge`` |
"""

from typing import Any
from uuid import uuid4

from jdparser.config import CONFIDENCE_THRESHOLD, JDParserError
from jdparser.extract import extract_jd_text
from jdparser.extract.fetch import fetch
from jdparser.extract.quality import check_quality
from jdparser.extract.resolve import resolve_final_url
from jdparser.graph.state import JobEvalState
from jdparser.llm.fit_judge import judge_fit
from jdparser.llm.jd_parser import parse_jd_requirements
from jdparser.llm.schemas import (
    ErrorRecord,
    EvaluatedJob,
    FailureStage,
    FetchResult,
    FitJudgment,
    JobRequirements,
    JobStatus,
    ResumeProfile,
)

# reason: heterogeneous LangGraph state payloads (SPEC §3.2/§3.9)
NodeResult = dict[str, Any]
Marker = dict[str, Any]  # reason: EvaluatedJob payload + transient _code/_msg (SPEC §3.2)


# --- helpers -----------------------------------------------------------------
def _job_meta(s: JobEvalState) -> tuple[str, str, str, str]:
    """(job_id, title, company, location) from the raw Adzuna job dict (§3.8.1)."""
    job = s["job"]
    job_id = str(job.get("id") or f"surrogate:{uuid4()}")
    title = job.get("title") or ""
    company = (job.get("company") or {}).get("display_name") or ""
    location = (job.get("location") or {}).get("display_name") or ""
    return job_id, title, company, location


def _jd_char_len(s: JobEvalState) -> int | None:
    quality = s.get("jd_quality")
    return quality["char_len"] if quality else None


def _marker(s: JobEvalState, stage: FailureStage, code: str, msg: str) -> Marker:
    """Build a ``failed`` EvaluatedJob marker plus transient ``_code`` / ``_msg``.

    ``final_url`` / ``requirements`` / ``judgment`` / ``jd_char_len`` are filled from
    whatever is present in ``s`` (mostly ``None`` on early-stage failures)."""
    job_id, title, company, location = _job_meta(s)
    raw_req = s.get("requirements")
    raw_judg = s.get("judgment")
    ej = EvaluatedJob(
        job_id=job_id,
        title=title,
        company=company,
        location=location,
        final_url=s.get("final_url"),
        source=s["job"],
        jd_char_len=_jd_char_len(s),
        requirements=JobRequirements.model_validate(raw_req) if raw_req else None,
        judgment=FitJudgment.model_validate(raw_judg) if raw_judg else None,
        status="failed",
        failure_stage=stage,
    )
    marker: Marker = ej.model_dump()
    marker["_code"] = code
    marker["_msg"] = msg
    return marker


def _fail(s: JobEvalState, stage: FailureStage, exc: JDParserError) -> Marker:
    return _marker(s, stage, exc.code, exc.message)


def _fail_code(s: JobEvalState, stage: FailureStage, code: str) -> Marker:
    return _marker(s, stage, code, code)


def _derive_status(j: FitJudgment) -> JobStatus:
    """SPEC §3.9 derivation table (single source of truth)."""
    if j.decision == "qualified":
        return "qualified" if j.confidence >= CONFIDENCE_THRESHOLD else "uncertain"
    if j.decision == "uncertain":
        return "uncertain"
    return "not_qualified"  # j.decision == "not_qualified"


def _evaluated_job(s: JobEvalState) -> NodeResult:
    """Build a SUCCESS EvaluatedJob (status per §3.9 table; ``failure_stage=None``)."""
    job_id, title, company, location = _job_meta(s)
    raw_req = s["requirements"]
    raw_judg = s["judgment"]
    assert raw_req is not None and raw_judg is not None  # set by parse/judge (success path)
    judgment = FitJudgment.model_validate(raw_judg)
    ej = EvaluatedJob(
        job_id=job_id,
        title=title,
        company=company,
        location=location,
        final_url=s.get("final_url"),
        source=s["job"],
        jd_char_len=_jd_char_len(s),
        requirements=JobRequirements.model_validate(raw_req),
        judgment=judgment,
        status=_derive_status(judgment),
        failure_stage=None,
    )
    return ej.model_dump()


# --- stage nodes -------------------------------------------------------------
# NOTE: node functions registered via ``add_node`` MUST name their first parameter
# ``state`` — LangGraph's ``_Node`` callback Protocol declares ``__call__(self,
# state: ...)``, so mypy --strict matches that parameter by name. (Routers passed to
# ``add_conditional_edges`` are unaffected and keep ``s``.)
def resolve_url(state: JobEvalState) -> NodeResult:
    try:
        return {"final_url": resolve_final_url(state["job"].get("redirect_url", ""))}
    except JDParserError as e:
        return {"result": [_fail(state, "resolve", e)]}     # marker consumed by router


def fetch_page(state: JobEvalState) -> NodeResult:
    final_url = state["final_url"]
    assert final_url is not None  # set by resolve_url (success path)
    try:
        fr = fetch(final_url)
        return {"fetched": fr.model_dump()}
    except JDParserError as e:
        return {"result": [_fail(state, "fetch", e)]}


def extract_jd(state: JobEvalState) -> NodeResult:
    raw_fetched = state["fetched"]
    assert raw_fetched is not None  # set by fetch_page (success path)
    text = extract_jd_text(FetchResult.model_validate(raw_fetched))
    if not text:
        return {"result": [_fail_code(state, "extract", "JD_NOT_FOUND")]}
    return {"jd_text": text}


def check_jd(state: JobEvalState) -> NodeResult:
    jd_text = state["jd_text"]
    assert jd_text is not None  # set by extract_jd (success path)
    q = check_quality(jd_text)
    if not q.passed:
        return {"jd_quality": q.model_dump(), "result": [_fail_code(state, "quality", q.reasons[0])]}
    return {"jd_quality": q.model_dump()}


def parse_requirements(state: JobEvalState) -> NodeResult:
    jd_text = state["jd_text"]
    assert jd_text is not None
    try:
        req = parse_jd_requirements(jd_text)             # Gemini 3.1 Flash Lite
        return {"requirements": req.model_dump()}
    except JDParserError as e:
        return {"result": [_fail(state, "parse", e)]}


def judge_fit_node(state: JobEvalState) -> NodeResult:
    raw_req = state["requirements"]
    assert raw_req is not None
    try:
        profile = ResumeProfile.model_validate(state["profile"])    # injected via Send (SPEC §3.2)
        req = JobRequirements.model_validate(raw_req)
        title = state["job"].get("title") or ""          # job title is the clearest seniority signal
        j = judge_fit(profile, req, title)               # GLM 5.2
        return {"judgment": j.model_dump()}
    except JDParserError as e:
        return {"result": [_fail(state, "judge", e)]}


# --- terminal nodes ----------------------------------------------------------
def finalize(state: JobEvalState) -> NodeResult:
    ej = _evaluated_job(state)                           # status per §3.9 table
    return {"evaluated_jobs": [ej]}


def record_failure(state: JobEvalState) -> NodeResult:
    marker = state["result"][0]                          # EvaluatedJob payload + transient _code/_msg
    err = ErrorRecord(
        job_id=marker["job_id"],
        stage=marker["failure_stage"],
        code=marker.get("_code", "UNKNOWN"),
        message=marker.get("_msg", ""),
    ).model_dump()
    clean = {k: v for k, v in marker.items() if k not in ("_code", "_msg")}  # drop transients
    return {"evaluated_jobs": [clean], "errors": [err]}


# --- routers (conditional edge fns: failure marker -> record_failure | next) -
def route_after_resolve(s: JobEvalState) -> str:
    return "record_failure" if s.get("result") else "fetch_page"


def route_after_fetch(s: JobEvalState) -> str:
    return "record_failure" if s.get("result") else "extract_jd"


def route_after_extract(s: JobEvalState) -> str:
    return "record_failure" if s.get("result") else "check_jd"


def route_after_check(s: JobEvalState) -> str:
    return "record_failure" if s.get("result") else "parse_requirements"


def route_after_parse(s: JobEvalState) -> str:
    return "record_failure" if s.get("result") else "judge_fit"


def route_after_judge(s: JobEvalState) -> str:
    return "record_failure" if s.get("result") else "finalize"
