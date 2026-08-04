# `cache/` — the resume-profile cache

Skip the expensive profiler LLM call for a resume we have already parsed. Owning spec:
[`docs/IMPLEMENTATION_CACHE.md`](../../../../docs/IMPLEMENTATION_CACHE.md).

| Module | What it holds |
|---|---|
| `fingerprint.py` | `cache_key = sha256(text_hash : PARSER_VERSION : SCHEMA_VERSION)`. |
| `store.py` | Content-addressed JSON files at `data/profiles/{cache_key}.json`, written atomically (temp + `os.replace`). |

The key folds in the parser/schema versions on purpose: bumping either invalidates every
cached profile so it re-parses under the current logic. Old files are orphaned, not
deleted, and that is harmless. Note the key is over extracted TEXT, so two byte-different
files that extract identically share one entry.
