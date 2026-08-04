"""Adzuna discovery client — SPEC §3.8.1, §4.5, §6.1, §6.4.

Executes a validated search plan against the Adzuna jobs API and normalizes the
results into ``Job``. ``run_search_plan`` raises on the first failure; the graph node
owns the continue-on-partial-failure policy (IMPLEMENTATION_ADZUNA.md — canonical).

**This module is the ONLY place that reads Adzuna's raw JSON shape.** Everything
downstream takes ``Job`` (see ``jdparser/jobs.py``).
"""

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


def search(query: AdzunaQuery, page: int) -> list[Job]:
    """One Adzuna API page (``page`` is a 1-based path segment) → normalized ``Job``s.

    401/403 → ``ADZUNA_AUTH``; any other status ≥ 400 → ``ADZUNA_HTTP``.
    """
    url = f"{ADZUNA_BASE_URL}/jobs/{ADZUNA_COUNTRY}/search/{page}"
    transport = httpx.HTTPTransport(retries=HTTP_MAX_RETRIES)
    with httpx.Client(timeout=FETCH_TIMEOUT_S, transport=transport) as c:
        r = c.get(url, params=_params(query))
    if r.status_code in (401, 403):
        raise JDParserError(code="ADZUNA_AUTH", message=r.text[:200])
    if r.status_code >= 400:
        raise JDParserError(code="ADZUNA_HTTP", message=f"{r.status_code}: {r.text[:200]}")
    # reason: heterogeneous Adzuna JSON passthrough (SPEC §3.8.1)
    results: list[dict[str, Any]] = r.json().get("results", [])
    return [_to_job(raw) for raw in results]


def run_search_plan(plan: list[AdzunaQuery]) -> list[Job]:
    """All queries × pages, flattened.

    Raises on the first failure (the graph node decides whether to continue on
    partial failure — IMPLEMENTATION_ADZUNA.md). No try/except here by design.
    """
    out: list[Job] = []
    for q in plan:
        for page in range(1, q.pages + 1):  # q.pages ≤ ADZUNA_MAX_PAGES
            out.extend(search(q, page))
    return out
