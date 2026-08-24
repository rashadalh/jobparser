"""Resume fingerprinting — SPEC §3.8.4, §4.7, IMPLEMENTATION_CACHE.

The ``cache_key`` folds in the parser/schema versions so a version bump is a
cache miss and the profiler re-runs. The prior file is not discarded: listing
still offers it, and a re-parse of the same text inherits its notes (see
``store.find_prior_profile``). The producing model is recorded in
``StoredResumeProfile.model`` for audit only — not in the key.
"""

import hashlib

from jdparser.config import PARSER_VERSION, SCHEMA_VERSION
from jdparser.llm.schemas import Fingerprint


def make_cache_key(text: str, parser_version: str, schema_version: str) -> str:
    """``sha256(sha256(text) : parser_version : schema_version)`` — one formula."""
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return hashlib.sha256(
        f"{text_hash}:{parser_version}:{schema_version}".encode("utf-8")
    ).hexdigest()


def compute_fingerprint(file_path: str, text: str) -> Fingerprint:
    """Hash normalized text into a content-addressed key.

    Invariant: identical text + same parser/schema versions => identical
    ``cache_key``.
    """
    return Fingerprint(cache_key=make_cache_key(text, PARSER_VERSION, SCHEMA_VERSION))
