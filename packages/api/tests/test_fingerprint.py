"""Tests for cache/fingerprint.compute_fingerprint (SPEC §3.8.4)."""

import hashlib
from pathlib import Path

from jdparser.cache.fingerprint import compute_fingerprint
from jdparser.config import PARSER_VERSION, SCHEMA_VERSION


def test_identical_text_different_files(tmp_path: Path) -> None:
    """Byte-different files with identical text share text_hash/cache_key but not
    file_hash."""
    text = "Senior Python engineer with 10 years of distributed systems experience."
    f1 = tmp_path / "a.txt"
    f2 = tmp_path / "b.txt"
    f1.write_bytes(b"first file bytes")
    f2.write_bytes(b"completely different bytes for the second file")

    fp1 = compute_fingerprint(str(f1), text)
    fp2 = compute_fingerprint(str(f2), text)

    assert fp1.text_hash == fp2.text_hash
    assert fp1.cache_key == fp2.cache_key
    assert fp1.file_hash != fp2.file_hash


def test_cache_key_matches_formula_and_version_bump(tmp_path: Path) -> None:
    """cache_key == sha256(text_hash:PARSER_VERSION:SCHEMA_VERSION); a bumped
    version string yields a different key (verified by recomputing inline)."""
    text = "Backend engineer skilled in Python, Go, and Kubernetes."
    f = tmp_path / "resume.txt"
    f.write_bytes(b"resume bytes")

    fp = compute_fingerprint(str(f), text)

    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    expected_key = hashlib.sha256(
        f"{text_hash}:{PARSER_VERSION}:{SCHEMA_VERSION}".encode("utf-8")
    ).hexdigest()

    assert fp.text_hash == text_hash
    assert fp.cache_key == expected_key

    bumped_key = hashlib.sha256(
        f"{text_hash}:9.9.9:{SCHEMA_VERSION}".encode("utf-8")
    ).hexdigest()
    assert bumped_key != fp.cache_key
