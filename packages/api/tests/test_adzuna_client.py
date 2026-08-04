"""Adzuna client tests (respx-mocked; NO network) — SPEC §4.5, §6.4.

All HTTP is intercepted by ``respx`` (which patches
``httpx.HTTPTransport.handle_request`` at the class level, so it intercepts even
though ``search`` builds its own custom transport). Zero live Adzuna calls.
"""

from typing import Any

import httpx
import pytest
import respx

from jdparser.adzuna.client import _params, _to_job, run_search_plan, search
from jdparser.config import ADZUNA_BASE_URL, ADZUNA_COUNTRY, JDParserError
from jdparser.llm.schemas import AdzunaQuery

_SEARCH_BASE = f"{ADZUNA_BASE_URL}/jobs/{ADZUNA_COUNTRY}/search"


# --- _params (pure) ----------------------------------------------------------
def test_params_always_includes_core_fields() -> None:
    p = _params(AdzunaQuery(what="backend engineer"))
    assert p["what"] == "backend engineer"
    assert "app_id" in p and "app_key" in p
    assert p["results_per_page"] == 20  # schema default (§6.1)


def test_params_boolean_flags_only_when_true() -> None:
    """full_time=1 present only when True; absent for None and False; never 0."""
    assert _params(AdzunaQuery(what="x", full_time=True))["full_time"] == 1
    assert "full_time" not in _params(AdzunaQuery(what="x"))  # default None
    assert "full_time" not in _params(AdzunaQuery(what="x", full_time=False))
    # mix: only the True flags appear
    p = _params(AdzunaQuery(what="x", contract=True, permanent=False, part_time=None))
    assert p["contract"] == 1
    assert "permanent" not in p and "part_time" not in p


def test_params_optional_fields_only_when_set() -> None:
    bare = _params(AdzunaQuery(what="x"))
    for k in ("where", "distance", "max_days_old", "category", "salary_min", "what_exclude"):
        assert k not in bare
    rich = _params(
        AdzunaQuery(
            what="x",
            where="Austin, TX",
            distance=25,
            max_days_old=14,
            category="it-jobs",
            salary_min=120000,
            what_exclude="manager",
        )
    )
    assert rich["where"] == "Austin, TX"
    assert rich["distance"] == 25
    assert rich["max_days_old"] == 14
    assert rich["category"] == "it-jobs"
    assert rich["salary_min"] == 120000
    assert rich["what_exclude"] == "manager"


def test_params_zero_distance_omitted() -> None:
    """distance=0 is falsy → omitted (truthy-only inclusion)."""
    assert "distance" not in _params(AdzunaQuery(what="x", distance=0))


# --- _to_job (pure) — the ONLY place raw provider JSON is read ----------------
def test_to_job_normalizes_nested_fields() -> None:
    raw = {
        "id": 12345,  # Adzuna sends this as a number
        "title": "Backend Engineer",
        "company": {"display_name": "Acme Inc"},
        "location": {"display_name": "Austin, TX"},
        "description": "We are hiring…",
        "redirect_url": "https://www.adzuna.com/land/ad/1",
    }
    job = _to_job(raw)
    assert (job.id, job.title, job.company, job.location) == (
        "12345", "Backend Engineer", "Acme Inc", "Austin, TX",
    )
    assert job.raw == raw               # provider payload preserved verbatim
    assert job.is_recruitment_agency is False   # set later, at the relevance screen


def test_to_job_survives_present_but_null_nested_keys() -> None:
    """Regression (REFACTOR_AUDIT F1): a key PRESENT but null is not a key absent.

    `.get("company", {})` returns the default only when the key is MISSING; Adzuna also
    sends `"company": null`, which yielded None and raised on the chained `.get`. Because
    dedupe_jobs is a top-level graph node with no try/except, that AttributeError ended the
    WHOLE run — one malformed listing lost every other job in the pull. Normalization now
    happens here, once, so this is the single place the guarantee has to hold.
    """
    job = _to_job({"id": "1", "title": None, "company": None, "location": None,
                   "description": None, "redirect_url": None})
    assert (job.title, job.company, job.location, job.description, job.redirect_url) == (
        "", "", "", "", "",
    )


def test_to_job_survives_entirely_absent_keys() -> None:
    job = _to_job({})
    assert job.id == "" and job.company == "" and job.redirect_url == ""


# --- search (respx) ----------------------------------------------------------
@respx.mock
def test_search_200_returns_results() -> None:
    jobs: list[dict[str, Any]] = [
        {"id": "1", "title": "Backend Engineer"},
        {"id": "2", "title": "SRE"},
    ]
    route = respx.get(url__startswith=_SEARCH_BASE).mock(
        return_value=httpx.Response(200, json={"count": 2, "results": jobs})
    )
    out = search(AdzunaQuery(what="backend engineer"), page=1)
    assert [(j.id, j.title) for j in out] == [("1", "Backend Engineer"), ("2", "SRE")]
    # `raw` carries the provider payload through untouched — EvaluatedJob.source is
    # persisted from it, so it must not be rebuilt from the normalized fields.
    assert [j.raw for j in out] == jobs
    assert route.called


@respx.mock
def test_search_200_missing_results_key_returns_empty() -> None:
    respx.get(url__startswith=_SEARCH_BASE).mock(
        return_value=httpx.Response(200, json={"count": 0})
    )
    assert search(AdzunaQuery(what="x"), page=1) == []


@respx.mock
def test_search_401_raises_adzuna_auth() -> None:
    respx.get(url__startswith=_SEARCH_BASE).mock(
        return_value=httpx.Response(401, text="bad credentials")
    )
    with pytest.raises(JDParserError) as ei:
        search(AdzunaQuery(what="x"), page=1)
    assert ei.value.code == "ADZUNA_AUTH"


@respx.mock
def test_search_403_raises_adzuna_auth() -> None:
    respx.get(url__startswith=_SEARCH_BASE).mock(return_value=httpx.Response(403))
    with pytest.raises(JDParserError) as ei:
        search(AdzunaQuery(what="x"), page=1)
    assert ei.value.code == "ADZUNA_AUTH"


@respx.mock
def test_search_500_raises_adzuna_http() -> None:
    respx.get(url__startswith=_SEARCH_BASE).mock(
        return_value=httpx.Response(500, text="boom")
    )
    with pytest.raises(JDParserError) as ei:
        search(AdzunaQuery(what="x"), page=1)
    assert ei.value.code == "ADZUNA_HTTP"


@respx.mock
def test_page_is_path_segment() -> None:
    """page=2 → request path ends with /search/2 (path segment, not a param)."""
    route = respx.get(url__startswith=_SEARCH_BASE).mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    search(AdzunaQuery(what="x"), page=2)
    assert route.calls.last.request.url.path.endswith("/search/2")


@respx.mock
def test_request_carries_full_time_flag_only_when_true() -> None:
    """full_time=1 in the wire params when True; absent when None."""
    route = respx.get(url__startswith=_SEARCH_BASE).mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    search(AdzunaQuery(what="x", full_time=True), page=1)
    params_true = route.calls.last.request.url.params
    assert params_true["full_time"] == "1"

    search(AdzunaQuery(what="x"), page=1)  # full_time defaults to None
    params_none = route.calls.last.request.url.params
    assert "full_time" not in params_none


@respx.mock
def test_request_carries_where_distance_only_when_set() -> None:
    route = respx.get(url__startswith=_SEARCH_BASE).mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    search(AdzunaQuery(what="x"), page=1)
    bare = route.calls.last.request.url.params
    assert "where" not in bare and "distance" not in bare

    search(AdzunaQuery(what="x", where="Austin, TX", distance=25), page=1)
    rich = route.calls.last.request.url.params
    assert rich["where"] == "Austin, TX"
    assert rich["distance"] == "25"


# --- run_search_plan (respx) -------------------------------------------------
@respx.mock
def test_run_search_plan_issues_query_x_page_requests_and_flattens() -> None:
    """2 queries × pages=2 → 4 requests; results flattened in order."""
    route = respx.get(url__startswith=_SEARCH_BASE).mock(
        return_value=httpx.Response(200, json={"results": [{"id": "j"}]})
    )
    plan = [
        AdzunaQuery(what="backend engineer", pages=2),
        AdzunaQuery(what="platform engineer", pages=2),
    ]
    out = run_search_plan(plan)
    assert route.call_count == 4
    assert len(out) == 4  # one job per page, flattened
    assert all(j.raw == {"id": "j"} for j in out)


@respx.mock
def test_run_search_plan_raises_on_first_failure() -> None:
    """Module stays pure: it raises on the first failing query (graph owns
    continue-on-partial-failure)."""
    respx.get(url__startswith=_SEARCH_BASE).mock(return_value=httpx.Response(500))
    with pytest.raises(JDParserError) as ei:
        run_search_plan([AdzunaQuery(what="x", pages=1)])
    assert ei.value.code == "ADZUNA_HTTP"
