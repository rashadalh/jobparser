"""Resume text extraction — SPEC §3.6, §4.7, IMPLEMENTATION_CACHE.

`extract_text` turns a PDF/DOCX/TXT resume into normalized text. No OCR: a
scanned/image-only PDF yields ~empty text and surfaces as ``RESUME_EMPTY_TEXT``.
"""

import re
from pathlib import Path

from docx import Document
from pypdf import PdfReader

from jdparser.config import MIN_RESUME_CHARS, JDParserError

# Horizontal whitespace runs (spaces, tabs, etc.) — never matches a newline.
_HORIZONTAL_WS = re.compile(r"[^\S\n]+")
# 4+ newlines == >2 consecutive blank lines; collapse to a single blank line.
_BLANK_LINES = re.compile(r"\n{4,}")


def _extract_pdf(file_path: str) -> str:
    reader = PdfReader(file_path)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _extract_docx(file_path: str) -> str:
    document = Document(file_path)
    return "\n".join(paragraph.text for paragraph in document.paragraphs)


def _extract_txt(file_path: str) -> str:
    return Path(file_path).read_text(encoding="utf-8", errors="replace")


def _normalize(text: str) -> str:
    """Collapse runs of whitespace to single spaces per line, strip trailing
    spaces, collapse >2 consecutive blank lines to 1, strip the BOM."""
    text = text.replace("\ufeff", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _HORIZONTAL_WS.sub(" ", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip()


def extract_text(file_path: str) -> str:
    """PDF/DOCX/TXT -> normalized resume text (SPEC §3.6, §6.4).

    Raises ``JDParserError(code="RESUME_UNSUPPORTED_TYPE")`` for any other
    (or missing) extension, and ``JDParserError(code="RESUME_EMPTY_TEXT")`` when
    the normalized text is shorter than ``MIN_RESUME_CHARS``.
    """
    suffix = Path(file_path).suffix.lower()
    if suffix == ".pdf":
        raw = _extract_pdf(file_path)
    elif suffix == ".docx":
        raw = _extract_docx(file_path)
    elif suffix == ".txt":
        raw = _extract_txt(file_path)
    else:
        raise JDParserError(
            code="RESUME_UNSUPPORTED_TYPE",
            message=f"unsupported resume file type: {suffix or '(none)'}",
        )

    text = _normalize(raw)
    if len(text) < MIN_RESUME_CHARS:
        raise JDParserError(
            code="RESUME_EMPTY_TEXT",
            message=f"resume text too short: {len(text)} < {MIN_RESUME_CHARS} chars",
        )
    return text
