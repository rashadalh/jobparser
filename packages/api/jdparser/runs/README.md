# `runs/` — the run record

`data/runs/{run_id}.json` is the single source of truth for a run's status and results.
There is no push channel; the frontend polls this. Owning spec:
[`docs/IMPLEMENTATION_API.md`](../../../../docs/IMPLEMENTATION_API.md).

`store.py` seeds a `pending` record at `POST /api/runs`, then the background task drives it
to `running` and a terminal `completed`/`failed` via `update_run` (load, overlay,
re-validate, atomic write). Records are never auto-deleted.

`list_runs` skips records that fail validation rather than failing the whole listing. That
is deliberate resilience, but it means **a schema change that invalidates old records makes
them silently vanish from run history** — no error, no log. Widen a `Literal` rather than
replacing a persisted value.
