# IMPLEMENTATION_EXTRACT — JD evidence pipeline

> Owns URL resolution, fetching (static + Playwright fallback), JD text extraction
> (JSON-LD → ATS → readable), and quality checks. References: SPEC §3.8.2–§3.8.3,
> §4.6, §6.1, §6.4, §10 item 3.

## Purpose

Turn an Adzuna redirect into the employer's final URL, fetch the page (rendering
JS when needed), and recover **full** JD text as evidence — then gate it on length
and noise. No JD text ⇒ no qualification (SPEC §7 rule 1).

## Files this area owns

- `packages/api/jdparser/extract/resolve.py`
- `packages/api/jdparser/extract/fetch.py`
- `packages/api/jdparser/extract/jsonld.py`
- `packages/api/jdparser/extract/ats.py`
- `packages/api/jdparser/extract/readable.py`
- `packages/api/jdparser/extract/quality.py`
- `packages/api/jdparser/extract/__init__.py` (exports `extract_jd_text`)
- tests: `tests/test_extract.py`, `tests/test_quality.py`

Must NOT edit `adzuna/`, `graph/`, `llm/`.

## `resolve.py`

```python
def resolve_final_url(redirect_url: str) -> str:
    """Follow the Adzuna redirect chain to the employer page.
    httpx with follow_redirects=True, HEAD then GET fallback; return str(r.url).
    Raise JDParserError(code="RESOLVE_FAILED") on connection error / empty URL.
    """
```
Brittleness: some redirects only resolve via GET (HEAD 405). Try HEAD, fall back to
GET. Use `FETCH_TIMEOUT_S`. Do not download the body here beyond what redirects need.

## `fetch.py`

```python
from jdparser.llm.schemas import FetchResult

def fetch(url: str) -> FetchResult:
    """Static-first, Playwright-fallback.
    1) httpx GET (follow_redirects=True, FETCH_TIMEOUT_S, HTTP_MAX_RETRIES,
       User-Agent = HTTP_USER_AGENT). If 2xx and looks substantive (heuristic below),
       return FetchResult(source="http").
    2) Otherwise render with Playwright (sync API, chromium, PLAYWRIGHT_TIMEOUT_MS,
       wait_until="networkidle"), return FetchResult(source="playwright").
    Raise JDParserError(code="FETCH_FAILED") if both fail.
    """
```

"Looks substantive" heuristic (avoid rendering everything): the static HTML body
text length ≥ `MIN_JD_CHARS` **and** the page is not an obvious JS shell
(`<div id="root"></div>` / `__NEXT_DATA__`-only). If the static body is thin or a
shell, escalate to Playwright. Keep the heuristic in one place; document it inline.

> This escalation heuristic is **best-effort and not contract-critical**: getting it
> slightly wrong only changes *how* a page is fetched, never the qualification
> outcome (a too-thin result still fails the quality gate downstream). The exact
> shell-detection patterns are the implementer's choice within this rule.

Playwright (sync) usage inside a threadpool node:
```python
from playwright.sync_api import sync_playwright

def _render(url: str) -> str:
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        page = b.new_page(user_agent=HTTP_USER_AGENT)    # config / SPEC §6.1
        page.goto(url, wait_until="networkidle", timeout=PLAYWRIGHT_TIMEOUT_MS)
        html = page.content()
        b.close()
    return html
```

The httpx static path also sends `headers={"User-Agent": HTTP_USER_AGENT}`
(SPEC §6.1) — one UA constant for both fetch paths.
Env gotcha (BUILD.md): `uv run playwright install chromium` must have run, else this raises.
Launch one browser per call (simple, robust); a pool is a later optimization.

## `jsonld.py`

```python
def jobposting_jsonld(html: str) -> str | None:
    """Find <script type="application/ld+json"> blocks; if any parses to a
    JobPosting (@type == "JobPosting", possibly inside @graph), return its
    `description` (HTML-stripped to text). Else None.
    """
```
This is the **preferred** source (most ATSs emit JobPosting JSON-LD). Strip HTML
tags from `description` (bs4 `.get_text(" ")`), collapse whitespace.

## `ats.py`

```python
def ats_extract(html: str, url: str) -> str | None:
    """ATS-specific main-content selectors keyed off the host.
    Greenhouse  (boards.greenhouse.io)  -> #content / .job__description
    Lever       (jobs.lever.co)         -> .posting-page / .section-wrapper
    Ashby       (jobs.ashbyhq.com)      -> [class*=_descriptionText]
    Workday     (*.myworkdayjobs.com)   -> [data-automation-id=jobPostingDescription]
    Generic     -> None  (fall through to readable_text)
    Returns extracted text or None.
    """
```
Use bs4 + a small host→selector map. Selectors are brittle by nature — **call this
out**: if a selector misses, return None and let `readable_text` handle it; do not
hard-fail. Keep the map data-driven so adding an ATS is one row.

## `readable.py`

```python
def readable_text(html: str) -> str | None:
    """trafilatura.extract(html, include_comments=False, include_tables=True,
    favor_recall=True). Return cleaned main-content text or None."""
```
Pin trafilatura 2.0.0 exactly (1.x signatures differ — IMPLEMENTATION.md note).

## `__init__.py` — orchestration

```python
def extract_jd_text(fetched: FetchResult) -> str | None:
    """Try in order; first non-empty wins:
       jobposting_jsonld(html) -> ats_extract(html, url) -> readable_text(html).
    Return the text, or None if all three fail (-> JD_NOT_FOUND in the node)."""
```
Order is the locked decision (SPEC §10 item 3). Truncate the chosen text to
`MAX_JD_CHARS` before returning.

## `quality.py`

```python
from jdparser.llm.schemas import QualityResult

def check_quality(text: str) -> QualityResult:
    """char_len = len(text).
    Fail JD_TOO_SHORT if char_len < MIN_JD_CHARS.
    Fail JD_NOISY if the boilerplate ratio > JD_BOILERPLATE_MAX_RATIO, where
    boilerplate ratio = (count of lines matching nav/footer patterns) / total lines
    (patterns: 'cookie', 'privacy policy', 'sign in', 'subscribe', 'all rights
    reserved', nav-link-only lines). passed = no failures.
    Returns QualityResult(char_len, passed, reasons=[codes])."""
```
The reasons list holds machine codes (`JD_TOO_SHORT`, `JD_NOISY`) so the node maps
them to `ErrorRecord.code` directly.

> The boilerplate-ratio computation is a **best-effort heuristic**; the exact pattern
> list is the implementer's choice. `JD_TOO_SHORT` (the `MIN_JD_CHARS` length gate) is
> the hard, contract-critical check; `JD_NOISY` is a secondary safety net. Keep the
> pattern list small and in one place (a module-level constant) so it's tunable.

## Failure → stage mapping (this area's slice of the parallel-enum table)

| Function | On failure raises code | Subgraph node | `failure_stage` |
|---|---|---|---|
| `resolve_final_url` | `RESOLVE_FAILED` | `resolve_url` | `resolve` |
| `fetch` | `FETCH_FAILED` | `fetch_page` | `fetch` |
| `extract_jd_text → None` | `JD_NOT_FOUND` | `extract_jd` | `extract` |
| `check_quality.passed False` | `JD_TOO_SHORT`/`JD_NOISY` | `check_jd` | `quality` |

## Done when

- `tests/test_extract.py` (fixtures = recorded HTML, no network):
  - a Greenhouse JSON-LD fixture → `jobposting_jsonld` returns the full description;
  - a Lever fixture with no JSON-LD → `ats_extract` returns the posting body;
  - a generic blog-style fixture → `readable_text` returns main content;
  - `extract_jd_text` picks the first successful source in order.
- `tests/test_quality.py`: a 700-char clean JD passes; a 200-char snippet fails
  `JD_TOO_SHORT`; a nav-heavy page fails `JD_NOISY`.
- `fetch` static path is unit-tested with respx; the Playwright fallback is verified
  once by the orchestrator against a known JS-rendered listing (Tier-2 manual, since
  it needs a browser) — documented, not in the unit suite.
