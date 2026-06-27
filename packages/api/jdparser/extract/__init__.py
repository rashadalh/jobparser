"""JD evidence pipeline — SPEC §4.6, §10 item 3, IMPLEMENTATION_EXTRACT.

Orchestrates extraction over a fetched page: ``jobposting_jsonld`` →
``ats_extract`` → ``readable_text``, first non-empty wins (the order is the
locked decision, SPEC §10 item 3), truncated to ``MAX_JD_CHARS``.
"""

from jdparser.config import MAX_JD_CHARS
from jdparser.extract.ats import ats_extract
from jdparser.extract.jsonld import jobposting_jsonld
from jdparser.extract.readable import readable_text
from jdparser.llm.schemas import FetchResult

__all__ = ["extract_jd_text"]


def extract_jd_text(fetched: FetchResult) -> str | None:
    """Recover full JD text from a fetched page (SPEC §4.6).

    Tries JSON-LD → ATS selectors → readable text in that locked order and
    returns the first non-empty result truncated to ``MAX_JD_CHARS``. Returns
    ``None`` when all three fail (the ``extract_jd`` node raises ``JD_NOT_FOUND``).
    """
    html = fetched.html

    text = jobposting_jsonld(html)
    if not text:
        text = ats_extract(html, fetched.url)
    if not text:
        text = readable_text(html)

    if not text:
        return None
    return text[:MAX_JD_CHARS]
