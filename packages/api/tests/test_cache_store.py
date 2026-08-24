"""Tests for cache/store.{get_profile,put_profile} (SPEC §3.7, §3.7-lifecycle).

Uses unique ``test-`` cache keys written to the real PROFILES_DIR and cleans up
every file it creates (so the real cache dir is not polluted).
"""

import uuid
from collections.abc import Iterator

import pytest

from jdparser.cache.fingerprint import make_cache_key
from jdparser.cache.store import (
    find_prior_profile,
    get_profile,
    inherit_from_prior,
    put_profile,
    retire_superseded,
)
from jdparser.config import PARSER_VERSION, PROFILES_DIR, SCHEMA_VERSION
from builders import stored_profile
from jdparser.llm.schemas import CandidateNote, StoredResumeProfile


def _make_record(cache_key: str, created_at: str, updated_at: str) -> StoredResumeProfile:
    # PARSER_VERSION/SCHEMA_VERSION (not the builder's pinned literals): these records are
    # written and read back through the live cache, which keys on the current versions.
    return stored_profile(
        id=str(uuid.uuid4()),
        cache_key=cache_key,
        parser_version=PARSER_VERSION,
        schema_version=SCHEMA_VERSION,
        created_at=created_at,
        updated_at=updated_at,
    )


@pytest.fixture
def cache_key() -> Iterator[str]:
    key = "test-" + uuid.uuid4().hex
    yield key
    path = PROFILES_DIR / f"{key}.json"
    if path.exists():
        path.unlink()


def test_round_trip_pydantic_equal(cache_key: str) -> None:
    record = _make_record(cache_key, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
    put_profile(record)
    loaded = get_profile(cache_key)
    assert loaded is not None
    assert loaded == record


def test_overwrite_preserves_created_at(cache_key: str) -> None:
    first = _make_record(cache_key, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
    put_profile(first)

    second = _make_record(cache_key, "2099-12-31T00:00:00Z", "2026-06-26T12:00:00Z")
    put_profile(second)

    loaded = get_profile(cache_key)
    assert loaded is not None
    assert loaded.created_at == "2026-01-01T00:00:00Z"  # original preserved
    assert loaded.updated_at == "2026-06-26T12:00:00Z"  # refreshed
    assert loaded.profile.skills == second.profile.skills


def test_missing_key_returns_none() -> None:
    assert get_profile("test-missing-" + uuid.uuid4().hex) is None


def test_find_prior_profile_matches_old_version_of_same_text(cache_key: str) -> None:
    """A parser bump changes the current key; the old file is still this resume."""
    text = "Senior Python engineer with ten years of distributed systems experience."
    old_key = make_cache_key(text, "1.0.0", "1.0.0")
    note = CandidateNote(note="Will not relocate", kind="dealbreaker", source="s")
    # cache_key fixture is unused as the filename; we write under the reconstructed key
    # and clean both possible files so the live cache dir stays clean.
    extra = PROFILES_DIR / f"{old_key}.json"
    try:
        put_profile(
            stored_profile(
                cache_key=old_key,
                parser_version="1.0.0",
                schema_version="1.0.0",
                notes=[note],
                created_at="2026-01-01T00:00:00Z",
            )
        )
        found = find_prior_profile(text)
        assert found is not None
        assert found.cache_key == old_key
        assert found.notes == [note]
        assert find_prior_profile(text + " different") is None
    finally:
        if extra.exists():
            extra.unlink()


def test_inherit_and_retire_moves_notes_to_new_key(cache_key: str) -> None:
    """Re-parse after a bump keeps notes and retires the old-version file."""
    text = "Backend engineer skilled in Python, Go, and Kubernetes."
    old_key = make_cache_key(text, "1.0.0", "1.0.0")
    note = CandidateNote(note="No active clearance", kind="dealbreaker", source="s")
    old = stored_profile(
        id="kept-id",
        cache_key=old_key,
        parser_version="1.0.0",
        schema_version="1.0.0",
        notes=[note],
        created_at="2026-01-01T00:00:00Z",
    )
    new = stored_profile(
        id="fresh-id",
        cache_key=cache_key,
        parser_version=PARSER_VERSION,
        schema_version=SCHEMA_VERSION,
        created_at="2099-01-01T00:00:00Z",
    )
    old_path = PROFILES_DIR / f"{old_key}.json"
    try:
        put_profile(old)
        inherited, prior = inherit_from_prior(new, text)
        assert prior is not None and prior.cache_key == old_key
        assert inherited.id == "kept-id"
        assert inherited.created_at == "2026-01-01T00:00:00Z"
        assert inherited.notes == [note]
        put_profile(inherited)
        retire_superseded(prior, inherited.cache_key)
        assert get_profile(old_key) is None
        loaded = get_profile(cache_key)
        assert loaded is not None
        assert loaded.notes == [note]
        assert loaded.id == "kept-id"
    finally:
        if old_path.exists():
            old_path.unlink()
