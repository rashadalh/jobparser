"""URL resolution — SPEC §4.6, §6.4, IMPLEMENTATION_EXTRACT.

`resolve_final_url` follows an Adzuna redirect chain to the employer's final
page. Adzuna ``redirect_url`` is *always* a redirect (SPEC §3.8.1), never the
employer page.
"""

import httpx

from jdparser.config import FETCH_TIMEOUT_S, HTTP_USER_AGENT, HTTP_REFERER, JDParserError


def resolve_final_url(redirect_url: str) -> str:
    """Follow the redirect chain and return the final employer URL.

    Uses httpx with ``follow_redirects=True``. Tries ``HEAD`` first and falls
    back to ``GET`` because some redirect endpoints reject ``HEAD`` (405).
    Returns ``str(r.url)`` (the post-redirect URL).

    Raises ``JDParserError(code="RESOLVE_FAILED")`` on an empty input URL or any
    httpx transport/connection error (SPEC §6.4).
    """
    if not redirect_url or not redirect_url.strip():
        raise JDParserError(code="RESOLVE_FAILED", message="empty redirect URL")

    headers = {"User-Agent": HTTP_USER_AGENT, "Referer": HTTP_REFERER}
    try:
        with httpx.Client(
            follow_redirects=True, timeout=FETCH_TIMEOUT_S, headers=headers
        ) as client:
            try:
                response = client.head(redirect_url)
                if response.status_code >= 400:
                    # Endpoint rejected HEAD (e.g. 405) — retry with GET.
                    response = client.get(redirect_url)
            except httpx.HTTPError:
                # HEAD itself errored (some servers refuse it) — retry with GET.
                response = client.get(redirect_url)
            return str(response.url)
    except httpx.HTTPError as exc:
        raise JDParserError(code="RESOLVE_FAILED", message=str(exc)) from exc
