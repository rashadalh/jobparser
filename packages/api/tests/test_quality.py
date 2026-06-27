"""JD quality-gate tests — SPEC §3.8.3, §6.1, §6.4.

``check_quality`` returns machine codes in ``reasons`` (never raises).
``JD_TOO_SHORT`` is the hard length gate; ``JD_NOISY`` is the boilerplate net.
"""

from jdparser.config import MIN_JD_CHARS
from jdparser.extract.quality import check_quality

CLEAN_JD = (
    "We are seeking a Senior Backend Engineer to join our payments platform team.\n"
    "You will design, build, and operate high-throughput services that move money safely.\n"
    "Responsibilities include owning services end to end, from API design to production support.\n"
    "You will collaborate with product and data teams to deliver reliable, well-tested features.\n"
    "The ideal candidate has five or more years of experience building distributed systems.\n"
    "Strong knowledge of Python, relational databases, and event-driven architectures is required.\n"
    "Experience with Kubernetes, observability tooling, and on-call operations is a strong plus.\n"
    "We offer competitive compensation, equity, and a remote-friendly, collaborative culture.\n"
)


def test_clean_jd_passes() -> None:
    assert len(CLEAN_JD) >= 700
    result = check_quality(CLEAN_JD)
    assert result.passed is True
    assert result.reasons == []
    assert result.char_len == len(CLEAN_JD)


def test_short_snippet_fails_too_short() -> None:
    snippet = "We are hiring a backend engineer to join our growing team. " * 3
    assert len(snippet) < MIN_JD_CHARS
    result = check_quality(snippet)
    assert result.passed is False
    assert "JD_TOO_SHORT" in result.reasons


def test_boilerplate_heavy_fails_noisy() -> None:
    boiler = "\n".join(
        [
            "Cookie preferences",
            "Privacy policy",
            "Sign in",
            "Subscribe to our newsletter",
            "All rights reserved 2026",
            "Manage cookie consent",
            "Sign in to your account",
            "Privacy policy and terms of service",
            "Subscribe for updates",
            "We use cookies to improve your experience",
        ]
        * 3  # 30 boilerplate lines, comfortably over MIN_JD_CHARS
    )
    text = boiler + "\nWe are hiring a backend engineer to build resilient services."
    assert len(text) >= MIN_JD_CHARS  # long enough that JD_TOO_SHORT does not apply
    result = check_quality(text)
    assert result.passed is False
    assert "JD_NOISY" in result.reasons
    assert "JD_TOO_SHORT" not in result.reasons


def test_nav_link_only_lines_count_as_boilerplate() -> None:
    """Short, punctuation-free nav labels trip the boilerplate ratio."""
    nav = "\n".join(["Home", "About", "Careers", "Blog", "Contact", "Press", "Login"] * 4)
    result = check_quality(nav)
    assert "JD_NOISY" in result.reasons
