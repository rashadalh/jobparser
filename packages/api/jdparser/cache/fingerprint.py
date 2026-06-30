"""Resume fingerprinting — SPEC §3.8.4, §4.7, IMPLEMENTATION_CACHE.

The ``cache_key`` deliberately folds in the parser/schema versions so a version
bump invalidates the cache (old files orphaned, harmless). The producing model is
recorded in ``StoredResumeProfile.model`` for audit only — not in the key.
"""

import hashlib

from jdparser.config import PARSER_VERSION, SCHEMA_VERSION
from jdparser.llm.schemas import Fingerprint


def compute_fingerprint(file_path: str, text: str) -> Fingerprint:
    """Hash normalized text into a content-addressed key.

    Invariant: identical text + same parser/schema versions => identical
    ``cache_key``.
    """
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    cache_key = hashlib.sha256(
        f"{text_hash}:{PARSER_VERSION}:{SCHEMA_VERSION}".encode("utf-8")
    ).hexdigest()
    return Fingerprint(cache_key=cache_key)
