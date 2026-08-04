# `jobsource/` — where job postings come from

Everything provider-specific about discovery sits behind `JobSource`. Owning spec:
[`docs/IMPLEMENTATION_ADZUNA.md`](../../../../docs/IMPLEMENTATION_ADZUNA.md).

| Module | What it holds |
|---|---|
| `base.py` | The `JobSource` protocol: query schema, planner prompt, `search()`. |
| `__init__.py` | `SOURCE` — the active source, bound at import. |
| `adzuna/` | The one implementation: `client.py` (HTTP + `_to_job` normalization) and the planner prompt. |
| `dedupe.py` | Provider-agnostic: dedupe `Job`s on (url, company, title, location). |

`adzuna/client._to_job` is the **only** code in the repo that reads raw provider JSON.
Everything downstream takes `Job`. That is load-bearing, not stylistic: the null-handling
used to be copy-pasted across five modules, one copy drifted, and it crashed entire runs.

There is one implementation and the protocol is still worth having — see
[`docs/REFACTOR_AUDIT.md`](../../../../docs/REFACTOR_AUDIT.md) §4.2 before concluding
otherwise.
