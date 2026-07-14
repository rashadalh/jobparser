"""Page fetching — SPEC §3.8.2, §4.6, §6.1, §6.4, IMPLEMENTATION_EXTRACT.

Static-first: try httpx and, only when the static HTML is thin or an obvious
JS shell, escalate to a real browser (Playwright). One ``HTTP_USER_AGENT`` for
both paths (SPEC §6.1).
"""

from bs4 import BeautifulSoup

import httpx

from jdparser.config import (
    FETCH_TIMEOUT_S,
    HTTP_MAX_RETRIES,
    HTTP_USER_AGENT,
    JD_FETCH_REFERER,
    MIN_JD_CHARS,
    PLAYWRIGHT_TIMEOUT_MS,
    JDParserError,
)
from jdparser.llm.schemas import FetchResult


def _looks_substantive(html: str) -> bool:
    """Best-effort: does the static HTML already carry the JD?

    True when the page's *visible* body text (scripts/styles stripped) is at
    least ``MIN_JD_CHARS``. Stripping ``<script>``/``<style>`` before measuring
    is precisely what rules out an obvious JS shell — an empty ``<div id="root">``
    mount or a ``__NEXT_DATA__``-only page has no visible text once its hydration
    blob is removed, so it reads as thin and escalates to Playwright.

    This escalation heuristic is best-effort and NOT contract-critical: a wrong
    call only changes *how* a page is fetched, never the qualification outcome
    (a too-thin result still fails the downstream quality gate). It is kept in
    this one place (IMPLEMENTATION_EXTRACT).
    """
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "template", "noscript"]):
        tag.decompose()
    container = soup.body or soup
    visible: str = container.get_text(" ", strip=True)
    return len(visible) >= MIN_JD_CHARS


def _render(url: str, *, headless: bool = True) -> tuple[str, int, str]:
    """Render ``url`` in chromium and return (html, status, final_url).

    Launches one browser per call (simple + robust; a pool is a later
    optimization). Requires ``uv run playwright install chromium`` (BUILD.md);
    otherwise the launch raises and ``fetch`` surfaces ``FETCH_FAILED``.

    ``headless=False`` requires a display (the container runs Xvfb + sets
    ``DISPLAY`` at startup, see ``entrypoint.sh``) — reserved for the escalation
    in ``fetch()`` below, not the default path.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            page = browser.new_page(user_agent=HTTP_USER_AGENT)
            response = page.goto(
                url, wait_until="networkidle", timeout=PLAYWRIGHT_TIMEOUT_MS, referer=JD_FETCH_REFERER
            )
            status: int = response.status if response is not None else 200
            html: str = page.content()
            final_url: str = page.url
        finally:
            browser.close()
    return html, status, final_url


def fetch(url: str) -> FetchResult:
    """Static-first, Playwright-fallback, headed-retry-on-block (SPEC §4.6).

    1. httpx ``GET`` (``follow_redirects=True``, ``FETCH_TIMEOUT_S`` timeout,
       ``HTTP_MAX_RETRIES`` transport retries, ``HTTP_USER_AGENT``). If 2xx and
       :func:`_looks_substantive`, return ``FetchResult(source="http")``.
    2. Otherwise render with headless Playwright.
    3. If THAT render's status is not 2xx/3xx, retry ONCE with a non-headless
       (headed) browser. Some anti-bot WAFs block Playwright's headless Chromium
       outright (observed: Adzuna's own `/land/` pages return 403) while allowing
       identical automation from the SAME IP when headed — confirmed empirically,
       not theoretical. Headed rendering needs a real display and is heavier, so
       it's a targeted last resort keyed on a blocked-looking STATUS, not on thin
       content alone (a genuinely short JD is still a normal 2xx and never
       triggers this — only the quality gate catches that case).

    Raises ``JDParserError(code="FETCH_FAILED")`` if all paths fail (SPEC §6.4).
    """
    headers = {"User-Agent": HTTP_USER_AGENT, "Referer": JD_FETCH_REFERER}

    # (1) Static-first via httpx.
    try:
        transport = httpx.HTTPTransport(retries=HTTP_MAX_RETRIES)
        with httpx.Client(
            follow_redirects=True,
            timeout=FETCH_TIMEOUT_S,
            transport=transport,
            headers=headers,
        ) as client:
            response = client.get(url)
        if 200 <= response.status_code < 300 and _looks_substantive(response.text):
            return FetchResult(
                url=str(response.url),
                status=response.status_code,
                html=response.text,
                source="http",
            )
    except httpx.HTTPError:
        pass  # fall through to Playwright

    # (2) Playwright fallback (renders JS-gated pages).
    try:
        html, status, final_url = _render(url)
        if status >= 400:
            # looked blocked/denied, not just thin — one targeted headed retry
            html, status, final_url = _render(url, headless=False)
        return FetchResult(url=final_url, status=status, html=html, source="playwright")
    except Exception as exc:  # browser launch / nav / timeout failures
        raise JDParserError(code="FETCH_FAILED", message=str(exc)) from exc
