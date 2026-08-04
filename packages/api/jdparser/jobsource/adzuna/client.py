"""Adzuna discovery client — SPEC §3.8.1, §4.5, §6.1, §6.4.

Executes a validated search plan against the Adzuna jobs API and normalizes the
results into ``Job``. ``run_search_plan`` raises on the first failure; the graph node
owns the continue-on-partial-failure policy (IMPLEMENTATION_ADZUNA.md — canonical).

**This module is the ONLY place that reads Adzuna's raw JSON shape.** Everything
downstream takes ``Job`` (see ``jdparser/jobs.py``).
"""

import time
from typing import Any

import httpx

from jdparser.config import (
    ADZUNA_APP_ID,
    ADZUNA_APP_KEY,
    ADZUNA_BASE_URL,
    ADZUNA_COUNTRY,
    FETCH_TIMEOUT_S,
    HTTP_MAX_RETRIES,
    JDParserError,
)
from jdparser.jobs import Job
from jdparser.llm.schemas import AdzunaQuery


def _params(q: AdzunaQuery) -> dict[str, str | int]:
    """Build the Adzuna query params.

    Optional string/int filters are included only when truthy. Boolean flags
    (``full_time``/``part_time``/``contract``/``permanent``) are sent as ``1``
    only when ``True`` and omitted otherwise — Adzuna treats presence as the
    flag, so we never send ``0``. ``app_id``/``app_key``/``results_per_page``/
    ``what`` are always included.
    """
    p: dict[str, str | int] = {
        "app_id": ADZUNA_APP_ID,
        "app_key": ADZUNA_APP_KEY,
        "results_per_page": q.results_per_page,
        "what": q.what,
    }
    if q.what_or:
        p["what_or"] = q.what_or
    if q.what_exclude:
        p["what_exclude"] = q.what_exclude
    if q.where:
        p["where"] = q.where
    if q.distance:
        p["distance"] = q.distance
    if q.max_days_old:
        p["max_days_old"] = q.max_days_old
    if q.category:
        p["category"] = q.category
    if q.salary_min:
        p["salary_min"] = q.salary_min
    for flag in ("full_time", "part_time", "contract", "permanent"):
        if getattr(q, flag):  # only send when True
            p[flag] = 1
    return p


def _to_job(raw: dict[str, Any]) -> Job:
    """Normalize one raw Adzuna result into a ``Job``.

    Every nested access uses ``(x or {})`` rather than ``.get(k, {})``: Adzuna sends these
    keys present-but-null as well as absent, and a default only covers the absent case. That
    distinction is not academic — it crashed whole runs (REFACTOR_AUDIT F1).

    A result with no ``id`` gets an empty one; the subgraph substitutes a surrogate rather
    than dropping the job.
    """
    return Job(
        id=str(raw.get("id") or ""),
        title=raw.get("title") or "",
        company=(raw.get("company") or {}).get("display_name") or "",
        location=(raw.get("location") or {}).get("display_name") or "",
        description=raw.get("description") or "",
        redirect_url=raw.get("redirect_url") or "",
        final_url=raw.get("final_url"),
        raw=raw,
    )


# Transient upstream failures worth another attempt. 429 is rate limiting; 5xx here are
# Adzuna's load balancer, not our request — the same query usually succeeds moments later.
# Everything else (400s) is our problem and retrying just wastes the run's time.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


def _retry_after(response: httpx.Response, attempt: int) -> float:
    """Seconds to wait: the server's `Retry-After` if it sent a sane one, else backoff.

    Capped — Adzuna has been observed sending long `Retry-After` values that would stall
    the run past the frontend's poll timeout, at which point waiting is worse than failing
    the query and letting the other queries through.
    """
    header: str = response.headers.get("Retry-After", "")
    try:
        seconds = float(header)
    except ValueError:
        seconds = 0.0  # http-date form, or junk — fall through to backoff
    if 0 < seconds <= 10:
        return seconds
    return 0.5 * (2.0**attempt)  # 0.5s, 1s, 2s, … (float base: mypy types int**int as Any)


def _error_body(response: httpx.Response) -> str:
    """A short, useful error message — never a slab of HTML.

    A 503 from Adzuna returns their branded error PAGE, so the old `r.text[:200]` put
    `<!DOCTYPE html> <html> <!-- This file is managed by Chef -->…` in the run's audit
    panel: 200 characters that tell the user nothing. Their JSON errors are worth showing;
    their HTML is not.
    """
    content_type = response.headers.get("Content-Type", "")
    if "html" in content_type.lower() or response.text.lstrip()[:9].lower() == "<!doctype":
        return f"upstream returned an HTML error page ({len(response.text)} bytes)"
    return response.text[:200].strip() or "(empty response body)"


def search(query: AdzunaQuery, page: int) -> list[Job]:
    """One Adzuna API page (``page`` is a 1-based path segment) → normalized ``Job``s.

    401/403 → ``ADZUNA_AUTH``; any other status ≥ 400 → ``ADZUNA_HTTP``.

    Transient statuses (`_RETRY_STATUSES`) are retried up to ``HTTP_MAX_RETRIES`` times
    before giving up. This is a real request loop rather than the transport's `retries=`
    argument, which retries **connection** errors ONLY and silently does nothing for an
    HTTP error response — verified: `HTTPTransport(retries=5)` against a 503 issues
    exactly one request. A single blip in Adzuna's load balancer used to fail every query
    in the plan on its first attempt and return a run with zero jobs.
    """
    url = f"{ADZUNA_BASE_URL}/jobs/{ADZUNA_COUNTRY}/search/{page}"
    params = _params(query)
    # retries= still earns its place here: it covers connection-level failures, which the
    # loop below never sees (httpx raises rather than returning a response).
    transport = httpx.HTTPTransport(retries=HTTP_MAX_RETRIES)
    with httpx.Client(timeout=FETCH_TIMEOUT_S, transport=transport) as c:
        for attempt in range(HTTP_MAX_RETRIES + 1):
            r = c.get(url, params=params)
            if r.status_code not in _RETRY_STATUSES or attempt == HTTP_MAX_RETRIES:
                break
            time.sleep(_retry_after(r, attempt))

    if r.status_code in (401, 403):
        raise JDParserError(code="ADZUNA_AUTH", message=_error_body(r))
    if r.status_code >= 400:
        raise JDParserError(
            code="ADZUNA_HTTP", message=f"{r.status_code}: {_error_body(r)}"
        )
    # reason: heterogeneous Adzuna JSON passthrough (SPEC §3.8.1)
    results: list[dict[str, Any]] = r.json().get("results", [])
    return [_to_job(raw) for raw in results]


def run_search_plan(plan: list[AdzunaQuery]) -> list[Job]:
    """All queries × pages, flattened.

    Raises on the first failure (the graph node decides whether to continue on
    partial failure — IMPLEMENTATION_ADZUNA.md). No try/except here by design.

    ponytail: retries are per-request, so a TOTAL Adzuna outage pays the backoff on every
    query x page (~8 x 5 requests) before the run gives up — bounded, and well inside the
    frontend's 300s poll timeout, but wasteful. Add a circuit breaker (stop retrying after
    N consecutive failures across the plan) if full outages stop being rare.
    """
    out: list[Job] = []
    for q in plan:
        for page in range(1, q.pages + 1):  # q.pages ≤ ADZUNA_MAX_PAGES
            out.extend(search(q, page))
    return out
