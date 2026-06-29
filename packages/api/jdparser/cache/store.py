"""Durable resume-profile cache — SPEC §3.7, §3.7-lifecycle, §4.7.

Content-addressed JSON flat-files at ``data/profiles/{cache_key}.json`` (SPEC §9:
MVP-stubbed; eventual form is a managed store). The cache is application state that
survives across runs; it is never auto-evicted.
"""

import os
import tempfile

from jdparser.config import PROFILES_DIR
from jdparser.llm.schemas import StoredResumeProfile


def get_profile(cache_key: str) -> StoredResumeProfile | None:
    """Return the stored profile for ``cache_key``, or ``None`` on a cache miss."""
    path = PROFILES_DIR / f"{cache_key}.json"
    if not path.exists():
        return None
    return StoredResumeProfile.model_validate_json(path.read_text(encoding="utf-8"))


def list_profiles() -> list[StoredResumeProfile]:
    """All cached resume profiles, newest-updated first.

    A profile written under a prior ``SCHEMA_VERSION`` (e.g. before a field was added)
    fails validation against the current schema; such stale files are skipped rather
    than failing the whole listing — they are orphaned by the cache-key bump anyway.
    """
    out: list[StoredResumeProfile] = []
    for path in PROFILES_DIR.glob("*.json"):
        try:
            out.append(StoredResumeProfile.model_validate_json(path.read_text(encoding="utf-8")))
        except Exception:  # reason: skip a stale/partial cache file, don't fail the listing
            continue
    out.sort(key=lambda r: r.updated_at, reverse=True)
    return out


def put_profile(record: StoredResumeProfile) -> None:
    """Atomically write ``record`` to its ``cache_key`` slot (SPEC §3.7-lifecycle).

    ``created_at`` preservation is handled here, not by the caller: if a file
    already exists at this key, its ``created_at`` is kept and only ``updated_at``
    moves forward. Writes go to a temp file in the same dir, then ``os.replace``
    (atomic on POSIX).
    """
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    path = PROFILES_DIR / f"{record.cache_key}.json"

    existing = get_profile(record.cache_key)
    if existing is not None:
        record = record.model_copy(update={"created_at": existing.created_at})

    fd, tmp = tempfile.mkstemp(dir=PROFILES_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(record.model_dump_json(indent=2))
        os.replace(tmp, path)
    except BaseException:
        # Don't leave a stray temp file behind on a failed write.
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
