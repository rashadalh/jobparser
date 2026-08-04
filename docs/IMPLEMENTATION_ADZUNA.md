# IMPLEMENTATION_ADZUNA — discovery + dedupe

> Owns the Adzuna client and dedupe. References: SPEC §3.8.1, §4.5, §6.1, §6.4.

## Purpose

Execute a validated search plan against the Adzuna jobs API, flatten results,
preserve source metadata + redirect URLs, and merge duplicate listings.

## Files this area owns

- `packages/api/jdparser/jobsource/adzuna/client.py`
- `packages/api/jdparser/jobsource/dedupe.py`
- tests: `tests/test_dedupe.py` (+ Adzuna client tests via `respx`)

Must NOT edit `extract/`, `graph/`, `llm/`.

## Adzuna API (external contract — verify against live API before relying)

Endpoint (GET):
```
{ADZUNA_BASE_URL}/jobs/{ADZUNA_COUNTRY}/search/{page}
  ?app_id={ADZUNA_APP_ID}&app_key={ADZUNA_APP_KEY}
  &results_per_page=20
  &what=...&what_exclude=...&where=...&distance=...
  &max_days_old=...&category=...&salary_min=...
  &full_time=1&part_time=1&contract=1&permanent=1
```
- `page` is a **path** segment (1-based), not a query param.
- Boolean filters are sent as `1` when true, **omitted** when None/false (Adzuna
  treats presence as the flag). Never send `0`.
- Response: `{"count": int, "results": [<job dict §3.8.1>]}`.
- Auth failure → 401/403 → raise `ADZUNA_AUTH`. Other non-2xx → `ADZUNA_HTTP`.
- `redirect_url` in each result is an Adzuna redirect, never the employer page
  (resolved later in `extract/resolve.py`).

`ADZUNA_BASE_URL`, `ADZUNA_COUNTRY` from SPEC §6.1. Credentials from env
(`ADZUNA_APP_ID`, `ADZUNA_APP_KEY`).

## `jobsource/adzuna/client.py`

```python
import httpx
from jdparser.config import (ADZUNA_BASE_URL, ADZUNA_COUNTRY, ADZUNA_APP_ID,
                             ADZUNA_APP_KEY, FETCH_TIMEOUT_S, HTTP_MAX_RETRIES,
                             JDParserError)
from jdparser.llm.schemas import AdzunaQuery


def _params(q: AdzunaQuery) -> dict:
    p = {"app_id": ADZUNA_APP_ID, "app_key": ADZUNA_APP_KEY,
         "results_per_page": q.results_per_page, "what": q.what}
    if q.what_exclude: p["what_exclude"] = q.what_exclude
    if q.where:        p["where"] = q.where
    if q.distance:     p["distance"] = q.distance
    if q.max_days_old: p["max_days_old"] = q.max_days_old
    if q.category:     p["category"] = q.category
    if q.salary_min:   p["salary_min"] = q.salary_min
    for flag in ("full_time", "part_time", "contract", "permanent"):
        if getattr(q, flag):    # only send when True
            p[flag] = 1
    return p


def search(query: AdzunaQuery, page: int) -> list[dict]:
    url = f"{ADZUNA_BASE_URL}/jobs/{ADZUNA_COUNTRY}/search/{page}"
    transport = httpx.HTTPTransport(retries=HTTP_MAX_RETRIES)
    with httpx.Client(timeout=FETCH_TIMEOUT_S, transport=transport) as c:
        r = c.get(url, params=_params(query))
    if r.status_code in (401, 403):
        raise JDParserError(code="ADZUNA_AUTH", message=r.text[:200])
    if r.status_code >= 400:
        raise JDParserError(code="ADZUNA_HTTP", message=f"{r.status_code}: {r.text[:200]}")
    return r.json().get("results", [])


def run_search_plan(plan: list[AdzunaQuery]) -> list[dict]:
    out: list[dict] = []
    for q in plan:
        for page in range(1, q.pages + 1):        # q.pages ≤ ADZUNA_MAX_PAGES
            out.extend(search(q, page))
    return out
```

Failure handling: `search_jobs` node (graph) wraps `run_search_plan`; a single
query's failure should append an `ErrorRecord` and continue with remaining queries
(catch per-query). Implement per-query try/except inside `run_search_plan` OR in the
node — **decision: do it in the node** so this module stays pure (raises on the first
failure; node decides whether to continue). See IMPLEMENTATION_GRAPH §search_jobs.

> Drift note: this signature (`run_search_plan` raises on first failure) is canonical.
> The graph node owns the continue-on-partial-failure policy.

## `jobsource/dedupe.py`

```python
def _key(job: dict) -> tuple[str, str, str, str]:
    final = (job.get("final_url") or job.get("redirect_url") or "").strip().lower()
    company = (job.get("company", {}).get("display_name") or "").strip().lower()
    title = (job.get("title") or "").strip().lower()
    loc = (job.get("location", {}).get("display_name") or "").strip().lower()
    return (final, company, title, loc)


def dedupe(jobs: list[dict]) -> list[dict]:
    seen: set = set()
    out: list[dict] = []
    for j in jobs:
        k = _key(j)
        if k in seen:
            continue
        seen.add(k)
        out.append(j)
    return out
```

Note: at dedupe time `final_url` is usually absent (resolution happens later), so the
key falls back to `redirect_url` — adequate for first-pass dedupe. First occurrence
wins; order preserved.

## Done when

- `tests/test_dedupe.py`: two jobs with the same `(redirect_url, company, title,
  location)` collapse to one; case/whitespace differences still collapse; distinct
  jobs are preserved; order is stable.
- Adzuna client test (respx): a mocked 200 returns `results`; a mocked 401 raises
  `ADZUNA_AUTH`; a 500 raises `ADZUNA_HTTP`; boolean flags only appear in params when
  True; `page` is in the path.
