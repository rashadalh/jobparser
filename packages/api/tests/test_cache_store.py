"""Tests for cache/store.{get_profile,put_profile} (SPEC §3.7, §3.7-lifecycle).

Uses unique ``test-`` cache keys written to the real PROFILES_DIR and cleans up
every file it creates (so the real cache dir is not polluted).
"""

import uuid
from collections.abc import Iterator

import pytest

from jdparser.cache.store import get_profile, put_profile
from jdparser.config import PARSER_VERSION, PROFILES_DIR, SCHEMA_VERSION
from builders import stored_profile
from jdparser.llm.schemas import StoredResumeProfile


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
