"""Tests for jobsource.dedupe — SPEC §4.5.

Dedupe key = ``(final_url or redirect_url, company, title, location)`` each stripped +
lowercased. First occurrence wins, order preserved.

Operates on ``Job``, not raw provider JSON — the null-handling this module used to do by
hand now lives in ``jobsource.adzuna.client._to_job`` (see tests there for the F1 regression that
motivated it).
"""

from typing import Any

from jdparser.jobsource.dedupe import _key, dedupe
from jdparser.jobs import Job


def _job(
    *,
    redirect_url: str = "https://www.adzuna.com/land/ad/1?abc",
    company: str = "Acme Inc",
    title: str = "Senior Backend Engineer",
    location: str = "Austin, TX",
    id: str = "",
    description: str = "",
    final_url: str | None = None,
    **raw: Any,
) -> Job:
    return Job(
        id=id,
        title=title,
        company=company,
        location=location,
        description=description,
        redirect_url=redirect_url,
        final_url=final_url,
        raw=raw,
    )


def test_identical_jobs_collapse_to_one() -> None:
    """Two jobs with the same (redirect_url, company, title, location) → one."""
    jobs = [_job(id="a"), _job(id="b")]
    out = dedupe(jobs)
    assert len(out) == 1
    assert out[0].id == "a"  # first occurrence wins


def test_case_and_whitespace_variants_collapse() -> None:
    """Case/whitespace differences in any key component still collapse."""
    jobs = [
        _job(),
        _job(
            redirect_url="  https://www.adzuna.com/land/ad/1?abc  ",
            company="ACME INC",
            title="  senior BACKEND engineer ",
            location="austin, tx",
        ),
    ]
    assert len(dedupe(jobs)) == 1


def test_distinct_jobs_preserved() -> None:
    """Jobs differing in any single key component are kept distinct."""
    jobs = [
        _job(id="base"),
        _job(id="diff_url", redirect_url="https://www.adzuna.com/land/ad/2?z"),
        _job(id="diff_company", company="Globex"),
        _job(id="diff_title", title="Staff Backend Engineer"),
        _job(id="diff_loc", location="Remote, US"),
    ]
    out = dedupe(jobs)
    assert len(out) == 5
    assert [j.id for j in out] == ["base", "diff_url", "diff_company", "diff_title", "diff_loc"]


def test_order_is_stable() -> None:
    """Output preserves first-seen input order across mixed dup/unique input."""
    jobs = [
        _job(id="1", title="A"),
        _job(id="2", title="B"),
        _job(id="3", title="A"),  # dup of #1
        _job(id="4", title="C"),
        _job(id="5", title="B"),  # dup of #2
    ]
    out = dedupe(jobs)
    assert [j.id for j in out] == ["1", "2", "4"]


def test_final_url_takes_precedence_over_redirect_url() -> None:
    """When present, final_url is the URL component (redirect_url is fallback)."""
    a = _job(id="a", redirect_url="https://r/1", final_url="https://employer/job")
    b = _job(id="b", redirect_url="https://r/DIFFERENT", final_url="https://employer/job")
    out = dedupe([a, b])
    assert len(out) == 1  # same final_url collapses despite different redirect_url


def test_all_empty_fields_do_not_crash() -> None:
    """Jobs whose fields all normalized to "" key to ("","","","") and collapse."""
    sparse = [
        _job(id="empty1", redirect_url="", company="", title="", location=""),
        _job(id="titled", redirect_url="", company="", title="Engineer", location=""),
        _job(id="empty2", redirect_url="", company="", title="", location=""),
        _job(id="urled", redirect_url="https://r/x", company="", title="", location=""),
    ]
    out = dedupe(sparse)
    assert [j.id for j in out] == ["empty1", "titled", "urled"]


def test_empty_input_returns_empty() -> None:
    assert dedupe([]) == []


def test_key_normalization() -> None:
    """_key lowercases + strips each component and falls back to redirect_url."""
    k = _key(_job(redirect_url="  HTTPS://R/1 ", company=" Acme ", title=" T ", location=" L "))
    assert k == ("https://r/1", "acme", "t", "l")


def test_key_all_empty_fields_returns_empty_strings() -> None:
    assert _key(_job(redirect_url="", company="", title="", location="")) == ("", "", "", "")
