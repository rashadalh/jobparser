"""JD quality gate — SPEC §3.8.3, §4.6, §6.1, §6.4, IMPLEMENTATION_EXTRACT.

``check_quality`` returns machine codes in ``reasons`` (the ``check_jd`` node maps
them to ``ErrorRecord.code``); it never raises. ``JD_TOO_SHORT`` (the
``MIN_JD_CHARS`` length gate) is the hard, contract-critical check; ``JD_NOISY``
(boilerplate ratio) is a secondary safety net.
"""

import re

from jdparser.config import JD_BOILERPLATE_MAX_RATIO, MIN_JD_CHARS
from jdparser.llm.schemas import QualityResult

# Small, tunable nav/footer boilerplate substrings (matched case-insensitively).
# Kept in one place so it's easy to extend (IMPLEMENTATION_EXTRACT).
_BOILERPLATE_PATTERNS: tuple[str, ...] = (
    "cookie",
    "privacy policy",
    "sign in",
    "subscribe",
    "all rights reserved",
)

# Clause/sentence punctuation — its absence on a short line is the nav-link tell.
_SENTENCE_PUNCT = re.compile(r"[.!?:;,]")


def _is_boilerplate(line: str) -> bool:
    """Heuristic: is a non-empty line nav/footer boilerplate rather than JD prose?"""
    lowered = line.lower()
    if any(pattern in lowered for pattern in _BOILERPLATE_PATTERNS):
        return True
    # Nav-link-only line: very short and lacking any sentence punctuation
    # (real JD bullets/sentences run longer or carry ':'/',' etc.).
    words = line.split()
    return 1 <= len(words) <= 3 and not _SENTENCE_PUNCT.search(line)


def check_quality(text: str) -> QualityResult:
    """Gate extracted JD text on length and boilerplate ratio (SPEC §6.1).

    Fails ``JD_TOO_SHORT`` when ``char_len < MIN_JD_CHARS``; fails ``JD_NOISY``
    when the boilerplate ratio (boilerplate lines / non-empty lines) exceeds
    ``JD_BOILERPLATE_MAX_RATIO``. ``passed`` is true iff there are no failures.
    """
    char_len = len(text)
    reasons: list[str] = []

    if char_len < MIN_JD_CHARS:
        reasons.append("JD_TOO_SHORT")

    lines = [stripped for line in text.splitlines() if (stripped := line.strip())]
    if lines:
        boilerplate = sum(1 for line in lines if _is_boilerplate(line))
        ratio = boilerplate / len(lines)
        if ratio > JD_BOILERPLATE_MAX_RATIO:
            reasons.append("JD_NOISY")

    return QualityResult(char_len=char_len, passed=not reasons, reasons=reasons)
