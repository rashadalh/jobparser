# `schedule/` — daily Telegram recommendations

Lambda tick: day lock, S3 resume, existing graph, archive, Telegram recs.
Owning spec: [`specs/auto-job-recommendations/SPEC.md`](../../../../specs/auto-job-recommendations/SPEC.md).
Operator steps: [`infra/RUNBOOK.md`](../../../../infra/RUNBOOK.md).

| Module | What it holds |
|---|---|
| `handler.py` | Event parse, lock skip/claim, graph invoke, archive, notify wiring |
| `bootstrap.py` | Runtime secrets into environ, then exec RIC |
| `store.py` | S3 keys: resume, config, profiles, runs, day locks, notified set |
| `notify.py` | Recs format, Bot API send, notified-set merge |
| `schemas.py` | `DayLock`, `NotifiedSet`, `ArchivedRun`, `ScheduleResult` |
| `secrets.py` | Secrets Manager `SecretString` JSON object parse |

Cities and weekday 07:00 / 16-minute stagger live in `infra/scheduler.tf`, not
here. `search` on the event is only a path-safe lock-key slug.
