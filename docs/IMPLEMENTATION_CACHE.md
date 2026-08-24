# IMPLEMENTATION_CACHE — resume text + profile cache

> Owns resume text extraction, fingerprinting, and the durable JSON-flat-file
> resume-profile cache. References: SPEC §3.6–§3.8.4, §3.7-lifecycle, §6.1, §6.4.

## Purpose

Turn a resume file into normalized text, fingerprint it, and load/store the
expensive `ResumeProfile` so a known resume skips the profiler LLM call. The cache
is **application state** (survives across runs), distinct from the LangGraph
checkpointer.

## Files this area owns

- `packages/api/jdparser/resume/extract_text.py`
- `packages/api/jdparser/cache/fingerprint.py`
- `packages/api/jdparser/cache/store.py`
- tests: `tests/test_fingerprint.py`, `tests/test_cache_store.py`

It must NOT edit `config.py` beyond reading constants, nor any `graph/` file.

## `resume/extract_text.py`

```python
def extract_text(file_path: str) -> str:
    """PDF/DOCX/TXT -> normalized resume text.

    - .pdf  -> pypdf: concatenate page.extract_text() across pages
    - .docx -> python-docx: join paragraph.text
    - .txt  -> read utf-8 (errors="replace")
    Normalization: collapse runs of whitespace to single spaces per line,
    strip trailing spaces, collapse >2 blank lines to 1, strip BOM.
    Raises JDParserError(code="RESUME_UNSUPPORTED_TYPE") for other extensions,
    JDParserError(code="RESUME_EMPTY_TEXT") if len(text) < MIN_RESUME_CHARS.
    """
```

Failure modes:
- A scanned/image-only PDF yields ~empty text → `RESUME_EMPTY_TEXT`. We do **not**
  OCR (out of scope). Surface the error; the run fails at the resume stage.
- Extension detection is by suffix (lowercased); also sniff: if suffix missing,
  raise `RESUME_UNSUPPORTED_TYPE` rather than guess.

`MIN_RESUME_CHARS = 200` (SPEC §6.4 family).

## `cache/fingerprint.py`

```python
import hashlib
from jdparser.config import PARSER_VERSION, SCHEMA_VERSION
from jdparser.llm.schemas import Fingerprint   # Fingerprint lives in schemas.py (SPEC §3.8.4)


def compute_fingerprint(file_path: str, text: str) -> Fingerprint:
    # text_hash stays a local: it builds cache_key but is not itself stored.
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    cache_key = hashlib.sha256(
        f"{text_hash}:{PARSER_VERSION}:{SCHEMA_VERSION}".encode("utf-8")
    ).hexdigest()
    return Fingerprint(cache_key=cache_key)
```

Invariant: identical text + same parser/schema versions ⇒ identical `cache_key`.
A `PARSER_VERSION`/`SCHEMA_VERSION` bump ⇒ new key ⇒ cache miss so the profiler
re-runs. The old file stays listed; `find_prior_profile(text)` reconstructs the
historical key from each file's stored versions and `inherit_from_prior` copies
`id` / `created_at` / `notes` onto the new record, then `retire_superseded`
removes the old file.

## `cache/store.py`

```python
import json, os, tempfile
from pathlib import Path
from jdparser.config import PROFILES_DIR     # = data/profiles, created at import
from jdparser.llm.schemas import StoredResumeProfile


def get_profile(cache_key: str) -> StoredResumeProfile | None:
    path = PROFILES_DIR / f"{cache_key}.json"
    if not path.exists():
        return None
    return StoredResumeProfile.model_validate_json(path.read_text("utf-8"))


def put_profile(record: StoredResumeProfile) -> None:
    """Atomic write: temp file in same dir, then os.replace.

    created_at preservation is handled HERE, not by the caller: if a file already
    exists at this cache_key, read its created_at and keep it (only updated_at moves
    forward). See SPEC §3.7-lifecycle.
    """
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    path = PROFILES_DIR / f"{record.cache_key}.json"
    existing = get_profile(record.cache_key)
    if existing is not None:
        record = record.model_copy(update={"created_at": existing.created_at})
    fd, tmp = tempfile.mkstemp(dir=PROFILES_DIR, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(record.model_dump_json(indent=2))
    os.replace(tmp, path)   # atomic on POSIX
```

Lifecycle (SPEC §3.7-lifecycle): enter on miss, never auto-evict, in-place overwrite
is atomic, consumer is `load_or_parse_profile`.

## Done when

- `tests/test_fingerprint.py`: same text → same `cache_key`; different
  `PARSER_VERSION` → different `cache_key`. Note the key is content-addressed by TEXT:
  two byte-different files that extract to identical text share a cache entry, which is
  the intent (the same resume re-exported should not re-parse).
- `tests/test_cache_store.py`: `put_profile` then `get_profile` round-trips a
  `StoredResumeProfile` byte-for-byte (Pydantic-equal); overwriting the same key
  replaces atomically; `get_profile` on a missing key returns `None`.
- `extract_text` returns non-empty normalized text for a sample PDF, DOCX, and TXT
  fixture, and raises the right `code` for an unsupported type and an empty file.
