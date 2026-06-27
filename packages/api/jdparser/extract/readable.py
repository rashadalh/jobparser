"""Readable main-content extraction — SPEC §4.6, §10 item 3, IMPLEMENTATION_EXTRACT.

The final fallback in the JD-extraction chain: trafilatura recovers the main
article/body text from generic pages with no JSON-LD and no known ATS selectors.
trafilatura 2.0.0 is pinned exactly (1.x signatures differ).
"""

import trafilatura


def readable_text(html: str) -> str | None:
    """Return trafilatura's cleaned main-content text, or ``None``.

    ``favor_recall=True`` keeps more borderline content (we'd rather over-include
    than drop JD prose; the quality gate trims noise downstream).
    """
    result = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=True,
        favor_recall=True,
    )
    if not result:
        return None
    text = str(result).strip()
    return text or None
