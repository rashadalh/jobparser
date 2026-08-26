# IMPLEMENTATION — Daily job recommendations (overview)

> Table of contents + conventions for this plane. Detail:
> `IMPLEMENTATION_<AREA>.md`. Phases: `BUILD.md`. Contract: `SPEC.md`.
> Root graph conventions remain `docs/IMPLEMENTATION.md`.

## Milestones

| Milestone | What | Detail doc |
|---|---|---|
| **M0 — Foundations** | `boto3` pin + lock; `JDPARSER_DATA_DIR`; `schedule/` package stub + schemas; `.gitignore` infra; `.env.example` keys | this doc §Foundations |
| **M1 — Store** | S3 protocol, fake for tests, real boto3 client | `IMPLEMENTATION_STORE.md` |
| **M2 — Notify** | format + send + notified-set merge; urllib tests | `IMPLEMENTATION_NOTIFY.md` |
| **M3 — Handler** | day lock, bootstrap secrets, graph invoke, archive, dual-mode entrypoint | `IMPLEMENTATION_HANDLER.md` |
| **M4 — Infra** | Terraform + relay zip + push script + RUNBOOK | `IMPLEMENTATION_INFRA.md` |
| **M_final — Polish** | README section, compose health, doc pointers | `BUILD.md` |

M1 and M2 are independent after M0 (non-overlapping files) and may run in
parallel. M3 depends on M1+M2. M4 depends on M3 (image must exist conceptually). First Terraform **create** of
the Lambda needs a pushed ECR image; `ignore_changes` on `image_uri` only
ignores later tag drift.

## Cross-cutting conventions

- Inherit root conventions: snake_case JSON, `JDParserError(code=...)`,
  `datetime.now(timezone.utc).isoformat()`, `uv run` / no pip, `mypy --strict`.
- **Do not fork the graph.** Import `build_graph`, `initial_state`,
  `is_qualified`.
- **Schedule I/O is injectable.** Tests call `handler_with_deps` /
  `run_scheduled_search` (same kwargs: `event`, `store`, `send`,
  `invoke_graph`, `now_iso`). Production `handler` assumes bootstrap already
  exported runtime secrets. Same `*WithDeps` split as `black-scholes-binary`
  research handlers.
- **No secrets in logs or S3 run JSON.** Run JSON is the root `RunRecord`
  (job text already lives there today); do not add tokens.
- **Code samples in area docs are canonical signatures** unless marked
  `pseudocode`.

## Foundations — new pins only

Add to `packages/api/pyproject.toml` `[project.dependencies]`:

```
"boto3==1.43.79",
```

Then `cd packages/api && uv lock && uv sync`. Confirm `uv pip freeze | grep boto3`
shows `boto3==1.43.79`.

Dockerfile (M3, not M0): after `uv sync --frozen --no-dev` in the image:

```
RUN uv pip install --python /app/.venv/bin/python awslambdaric==4.0.2
```

Do **not** put `awslambdaric` in `pyproject.toml`. Do **not** use
`python -m pip` — the uv venv has no pip.

### `config.py` additions (M0)

```python
DATA_DIR: Path = Path(os.getenv("JDPARSER_DATA_DIR", str(Path(__file__).resolve().parent.parent / "data")))
# existing PROFILES_DIR / RUNS_DIR / UPLOADS_DIR derived from DATA_DIR — keep that.
# mkdir at import stays.

SCHEDULE_TZ: str = "America/Chicago"
SCHEDULE_LOCK_STALE_S: int = 900
TELEGRAM_CHUNK_CHARS: int = 3500
TELEGRAM_SEND_TIMEOUT_S: int = 10
```

`.env.example` additive keys (empty values): `SCHEDULE_BUCKET=`,
`RUNTIME_SECRET_ARN=`, `TELEGRAM_SECRET_ARN=`, `JDPARSER_DATA_DIR=`.

`.gitignore` additive:

```
infra/.terraform/
infra/*.tfstate
infra/*.tfstate.*
infra/*.tfplan
infra/.build/
infra/terraform.tfvars
infra/*.secret
infra/*.secret.json
!infra/*.secret.example.json
```
