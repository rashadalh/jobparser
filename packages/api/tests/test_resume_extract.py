"""Tests for resume/extract_text.extract_text (SPEC §3.6, §6.4)."""

from pathlib import Path

import pytest

from jdparser.config import JDParserError
from jdparser.resume.extract_text import extract_text

FIXTURES = Path(__file__).parent / "fixtures"


def test_extract_pdf() -> None:
    text = extract_text(str(FIXTURES / "sample_resume.pdf"))
    assert len(text) > 200
    assert "Python" in text


def test_extract_docx() -> None:
    text = extract_text(str(FIXTURES / "sample_resume.docx"))
    assert len(text) > 200
    assert "Python" in text


def test_extract_txt() -> None:
    text = extract_text(str(FIXTURES / "sample_resume.txt"))
    assert len(text) > 200
    assert "Python" in text


def test_unsupported_type_raises(tmp_path: Path) -> None:
    p = tmp_path / "resume.rtf"
    p.write_text("plenty of resume content here " * 20, encoding="utf-8")
    with pytest.raises(JDParserError) as exc:
        extract_text(str(p))
    assert exc.value.code == "RESUME_UNSUPPORTED_TYPE"


def test_empty_text_raises(tmp_path: Path) -> None:
    p = tmp_path / "tiny.txt"
    p.write_text("short", encoding="utf-8")
    with pytest.raises(JDParserError) as exc:
        extract_text(str(p))
    assert exc.value.code == "RESUME_EMPTY_TEXT"
