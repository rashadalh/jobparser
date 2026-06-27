"""ATS-specific extraction — SPEC §4.6, §10 item 3, IMPLEMENTATION_EXTRACT.

Host → main-content CSS selectors. Selectors are brittle by nature: a miss
returns ``None`` (never a hard fail) so ``readable_text`` can take over. The map
is data-driven — adding an ATS is one row.
"""

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

_WS = re.compile(r"\s+")

# One row per ATS: (host suffix, ordered CSS selectors — first match wins).
# A leading "." marks a wildcard-subdomain suffix (e.g. *.myworkdayjobs.com).
_ATS_SELECTORS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("boards.greenhouse.io", ("#content", ".job__description")),
    ("jobs.lever.co", (".posting-page", ".section-wrapper")),
    ("jobs.ashbyhq.com", ('[class*="_descriptionText"]',)),
    (".myworkdayjobs.com", ('[data-automation-id="jobPostingDescription"]',)),
)


def _selectors_for(host: str) -> tuple[str, ...] | None:
    """Map a hostname to its ATS selectors, or ``None`` for a generic host."""
    for suffix, selectors in _ATS_SELECTORS:
        if suffix.startswith("."):  # wildcard-subdomain suffix
            if host == suffix[1:] or host.endswith(suffix):
                return selectors
        elif host == suffix or host.endswith("." + suffix):
            return selectors
    return None


def ats_extract(html: str, url: str) -> str | None:
    """Return ATS main-content text for a known host, else ``None``.

    A known host whose selectors all miss also returns ``None`` (never a hard
    fail) — ``readable_text`` is the fallback.
    """
    host = (urlparse(url).hostname or "").lower()
    selectors = _selectors_for(host)
    if selectors is None:
        return None

    soup = BeautifulSoup(html, "lxml")
    for selector in selectors:
        node = soup.select_one(selector)
        if node is None:
            continue
        text = _WS.sub(" ", node.get_text(" ")).strip()
        if text:
            return text
    return None
