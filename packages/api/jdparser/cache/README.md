# `cache/` — the resume-profile cache

Skip the expensive profiler LLM call for a resume we have already parsed. Owning spec:
[`docs/IMPLEMENTATION_CACHE.md`](../../../../docs/IMPLEMENTATION_CACHE.md).

| Module | What it holds |
|---|---|
| `fingerprint.py` | `cache_key = sha256(text_hash : PARSER_VERSION : SCHEMA_VERSION)`. |
| `store.py` | Content-addressed JSON files at `data/profiles/{cache_key}.json`, written atomically (temp + `os.replace`). |

The key folds in the parser/schema versions on purpose: bumping either is a cache
miss so the profiler re-runs. Old files stay listed (and keep their notes); a
re-parse of the same text inherits those notes onto the new key. Note the key is
over extracted TEXT, so two byte-different files that extract identically share
one entry.
