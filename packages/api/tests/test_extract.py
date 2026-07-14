"""JD-extraction tests (recorded HTML fixtures; NO network, NO Playwright).

Covers the per-source extractors (``jobposting_jsonld`` / ``ats_extract`` /
``readable_text``), the locked orchestration order in ``extract_jd_text``
(SPEC §10 item 3), and the httpx static path of ``fetch`` via ``respx``. The
Playwright fallback is verified separately by the orchestrator against a real
browser — it is intentionally not exercised here.
"""

from pathlib import Path

import httpx
import pytest
import respx

from jdparser.config import MAX_JD_CHARS, MIN_JD_CHARS
from jdparser.extract import extract_jd_text
from jdparser.extract import fetch as fetch_mod
from jdparser.extract.ats import ats_extract
from jdparser.extract.fetch import fetch
from jdparser.extract.jsonld import jobposting_jsonld
from jdparser.extract.readable import readable_text
from jdparser.llm.schemas import FetchResult

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --- jobposting_jsonld -------------------------------------------------------
def test_jsonld_returns_stripped_description() -> None:
    text = jobposting_jsonld(_load("jd_greenhouse_jsonld.html"))
    assert text is not None
    assert len(text) >= MIN_JD_CHARS
    assert "autonomous fleet" in text
    assert "fault-tolerant microservices" in text
    # HTML tags from the JSON-LD description are stripped to plain text.
    assert "<p>" not in text and "<li>" not in text and "<strong>" not in text


def test_jsonld_handles_graph_nesting() -> None:
    """A JobPosting nested inside an ``@graph`` list is still found."""
    text = jobposting_jsonld(_load("jd_order_jsonld_and_ats.html"))
    assert text is not None
    assert "JSONLD_WINNER_MARKER" in text


def test_jsonld_returns_none_without_jobposting() -> None:
    assert jobposting_jsonld(_load("jd_blog.html")) is None


def test_jsonld_skips_malformed_blocks() -> None:
    html = '<script type="application/ld+json">{ not valid json,,, }</script>'
    assert jobposting_jsonld(html) is None


# --- ats_extract -------------------------------------------------------------
def test_ats_extract_lever_returns_posting_body() -> None:
    text = ats_extract(_load("jd_lever.html"), "https://jobs.lever.co/acme/123")
    assert text is not None
    assert len(text) >= MIN_JD_CHARS
    assert "LEVER_BODY_MARKER" in text
    assert "golden paths" in text


def test_ats_extract_generic_host_returns_none() -> None:
    assert ats_extract(_load("jd_lever.html"), "https://careers.example.com/123") is None


def test_ats_extract_workday_wildcard_subdomain() -> None:
    """A ``*.myworkdayjobs.com`` host is recognized (selector miss → None, not a crash)."""
    html = "<html><body><div>nothing here</div></body></html>"
    assert ats_extract(html, "https://acme.wd5.myworkdayjobs.com/job/123") is None


# --- readable_text -----------------------------------------------------------
def test_readable_text_returns_main_content() -> None:
    text = readable_text(_load("jd_blog.html"))
    assert text is not None
    assert "BLOG_BODY_MARKER" in text
    assert "remote build cache" in text


# --- extract_jd_text (locked order: jsonld > ats > readable) ------------------
def test_extract_order_jsonld_wins_over_ats() -> None:
    """A page with BOTH a JobPosting JSON-LD and a Greenhouse ATS body returns
    the JSON-LD text — proving JSON-LD wins the locked order (SPEC §10 item 3)."""
    fetched = FetchResult(
        url="https://boards.greenhouse.io/initech/jobs/123",
        status=200,
        html=_load("jd_order_jsonld_and_ats.html"),
        source="http",
    )
    text = extract_jd_text(fetched)
    assert text is not None
    assert "JSONLD_WINNER_MARKER" in text
    assert "ATS_GREENHOUSE_BODY_MARKER" not in text


def test_extract_falls_through_to_readable() -> None:
    """No JSON-LD and a generic (non-ATS) host → readable_text supplies the JD."""
    fetched = FetchResult(
        url="https://careers.example.com/blog/post",
        status=200,
        html=_load("jd_blog.html"),
        source="http",
    )
    text = extract_jd_text(fetched)
    assert text is not None
    assert "BLOG_BODY_MARKER" in text


def test_extract_returns_none_when_all_sources_empty() -> None:
    fetched = FetchResult(
        url="https://careers.example.com/x",
        status=200,
        html="<html><body></body></html>",
        source="http",
    )
    assert extract_jd_text(fetched) is None


def test_extract_truncates_to_max_jd_chars() -> None:
    big = "Build resilient backend services. " * 5000  # well over MAX_JD_CHARS
    html = f'<html><body><article><p>{big}</p></article></body></html>'
    fetched = FetchResult(
        url="https://careers.example.com/x", status=200, html=html, source="http"
    )
    text = extract_jd_text(fetched)
    assert text is not None
    assert len(text) == MAX_JD_CHARS


# --- fetch static path (respx; never reaches Playwright) ----------------------
@respx.mock
def test_fetch_static_substantive_returns_http_source() -> None:
    url = "https://jobs.example.com/listing/1"
    body = _load("jd_blog.html")
    route = respx.get(url).mock(return_value=httpx.Response(200, text=body))

    result = fetch(url)

    assert route.called
    assert result.source == "http"
    assert result.status == 200
    assert result.url == url
    assert "BLOG_BODY_MARKER" in result.html


# --- headed-retry escalation (mocks `_render` itself — real Playwright rendering
#     is intentionally not exercised here, matching this file's own convention) --
@respx.mock
def test_fetch_escalates_to_headed_when_headless_render_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A blocked-looking (non-2xx) headless render triggers exactly one headed retry,
    and that retry's result is what `fetch` returns."""
    url = "https://www.adzuna.com/land/ad/123"
    respx.get(url).mock(return_value=httpx.Response(403, text="denied"))  # httpx path fails too

    calls: list[bool] = []

    def fake_render(u: str, *, headless: bool = True) -> tuple[str, int, str]:
        calls.append(headless)
        if headless:
            return ("<html>unusual behaviour from your connection</html>", 403, u)
        return ("<html>the real long job description</html>", 200, u)

    monkeypatch.setattr(fetch_mod, "_render", fake_render)
    result = fetch(url)

    assert calls == [True, False]  # headless first, exactly one headed retry
    assert result.status == 200
    assert "real long job description" in result.html


@respx.mock
def test_fetch_does_not_escalate_when_headless_render_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A normal 2xx headless render — even with thin content — is NOT escalated to a
    headed retry. A genuinely short JD is a quality-gate failure, not a blocked fetch."""
    url = "https://jobs.example.com/thin-listing"
    respx.get(url).mock(return_value=httpx.Response(200, text="<html>too short</html>"))

    calls: list[bool] = []

    def fake_render(u: str, *, headless: bool = True) -> tuple[str, int, str]:
        calls.append(headless)
        return ("<html>short</html>", 200, u)

    monkeypatch.setattr(fetch_mod, "_render", fake_render)
    fetch(url)

    assert calls == [True]  # no headed retry
