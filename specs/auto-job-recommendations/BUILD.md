# BUILD — Daily job recommendations (orchestrator brief)

> The orchestrator reads this + the area doc for the active phase.
> Contract: `SPEC.md`. Root graph build is `/BUILD.md` (already shipped).

## Mission

Add a scheduled plane: 07:00 America/Chicago container Lambda runs the
existing matcher, archives the run to S3, and Telegrams new qualified jobs.
"Done" = SPEC §7.1–§7.7. The web app still works locally (SPEC §7.6).

## Hard rules (override agent judgment)

1. **Doc precedence.** This `SPEC.md` > this `IMPLEMENTATION_*` > agent
   judgment. Root `/SPEC.md` wins for graph / `is_qualified` / `RunRecord`.
2. **No scope creep.** SPEC §8. No web deploy, no Telegram commands, no
   DynamoDB, no schedule enable on first apply.
3. **No new dependencies / no silent upgrades.** Pins in this
   `IMPLEMENTATION.md` §Foundations + existing `docs/IMPLEMENTATION.md`.
   `awslambdaric` is Dockerfile-only. `boto3==1.43.79` is the only new
   pyproject pin.
4. **Type strictness.** `uv run mypy --strict` on `jdparser/schedule/`.
   `# reason:` on any ignore.
5. **Errors are typed.** `JDParserError(code=...)` with SPEC §4.6 codes.
6. **Contracts are spec-stable.** S3 keys, Telegram prefixes, day-lock
   lifecycle, `ScheduleResult` fields.
7. **Do not fork the graph.** Import `build_graph`, `initial_state`,
   `is_qualified`.
8. **Tests run.** Every new `tests/test_schedule_*.py` executed green.
9. **Compose still boots.** Image without `AWS_LAMBDA_RUNTIME_API` still
   starts uvicorn (SPEC §7.6).
10. **Terraform safety.** One root; schedules default DISABLED; secret
    `ignore_changes`; bucket `prevent_destroy`; saved plan before apply.
    No `-auto-approve` of a fresh plan.
11. **Commit each phase** with a discrete message.

## Environment gotchas (inherit + this plane)

- Root gotchas still apply (uv, bun, Python 3.11, Playwright chromium,
  Tailwind, secrets).
- **`awslambdaric` on macOS:** do not `uv add` it; Linux image only
  (`uv pip install --python` inside Dockerfile).
- **Lambda writable disk is `/tmp` only.** `JDPARSER_DATA_DIR=/tmp/jdparser-data`.
- **Arm64 image.** `docker build --platform linux/arm64`. On Apple Silicon
  this is native; on amd64 CI it needs buildx.
- **Xvfb** must still start in the Lambda process (entrypoint). Headed
  Playwright retry needs `DISPLAY=:99`.
- **Terraform not in the uv env.** Install Terraform ≥1.5 on the host.
  `cd infra && terraform init`.
- **First apply needs an ECR image.** Follow RUNBOOK target-then-push.
  `lifecycle.ignore_changes` does not allow CreateFunction without a digest.
- **Scheduler timezone attribute.** Input uses
  `<aws.scheduler.scheduled-time>` literally (AWS replaces it). Do not
  Terraform-template that string with `formatdate`. Parse it as UTC then
  convert to `America/Chicago` (SPEC §3.1).
- **OpenRouter/Adzuna keys** are not Lambda env vars; bootstrap writes them
  into the process environ before importing `jdparser`.

## Verification tiers

| Tier | Proves |
|---|---|
| 1 Compile | mypy, terraform validate, docker build |
| 2 Runtime data | pytest with fakes; lambda handler_with_deps |
| 3 User-facing | live `lambda invoke` + S3 object + Telegram (or documented manual) |

Tier 3.5 not required (SPEC §7).

Trust-but-verify: orchestrator re-runs the phase pytest / validate itself.

---

## Phase 0 — Foundations (single)

### Inputs
Root matcher already works (`/BUILD.md` complete).

### Ownership
`pyproject.toml` + `uv.lock` (boto3 pin); `config.py` DATA_DIR + schedule
constants; `jdparser/schedule/__init__.py` (empty or docstring);
`schedule/schemas.py` models; `.gitignore`; `.env.example`; `specs/` already
on disk.

Forbid: graph, web, infra apply.

### Exit-check (Tier 1)
`uv pip freeze | grep boto3` → `boto3==1.43.79`.
`uv run python -c "from jdparser.schedule.schemas import DayLock, NotifiedSet, ScheduleSearchConfig"`
succeeds. `uv run mypy --strict packages/api/jdparser/config.py packages/api/jdparser/schedule/schemas.py`.
`JDPARSER_DATA_DIR=/tmp/foo uv run python -c "from jdparser.config import DATA_DIR; assert str(DATA_DIR)=='/tmp/foo'"`.

### Commit
`Phase 0: schedule schemas + boto3 pin + DATA_DIR`

---

## Phase 1 — Store + Phase 2 — Notify (parallel-2 then integration)

### Inputs
Phase 0.

### Sub-agents
- **Store:** `IMPLEMENTATION_STORE.md`. Owns `schedule/store.py`,
  `tests/test_schedule_store.py`. Forbid `notify.py`, `handler.py`, `infra/`.
- **Notify:** `IMPLEMENTATION_NOTIFY.md`. Owns `schedule/notify.py`,
  `tests/test_schedule_notify.py`. Forbid `store.py` writes (import schemas
  only), `infra/`.

### Exit-check (Tier 2)
`uv run pytest tests/test_schedule_store.py tests/test_schedule_notify.py`
green (orchestrator, not self-report). Notify tests include the mid-batch
send failure prefix behavior (SPEC §3.4 add-on-success).

### Commit
`Phase 1+2: S3 store + Telegram notify`

---

## Phase 3 — Handler + dual-mode image (single)

### Inputs
Phases 1–2.

### Ownership
`IMPLEMENTATION_HANDLER.md` files.

### Exit-check (Tier 2 + 1)
`uv run pytest tests/test_schedule_handler.py tests/test_schedule_store.py tests/test_schedule_notify.py`
green. Tests must include SPEC §7.2, §7.3 (pre-seeded notified id), §7.4
(failed/uncertain not sent), §7.7 (lock ends failed). Orchestrator:
`docker compose build api` (or `docker build packages/api`) succeeds.
`docker compose up -d api` then `curl -s localhost:8000/api/health` contains
`"ok"` (SPEC §7.6). Tear down compose after.

Optional RIC smoke (not required to claim §7.6): `docker run --rm -e AWS_LAMBDA_RUNTIME_API=127.0.0.1:1`
the api image should start `bootstrap`/`awslambdaric` rather than uvicorn
(log line); it will then fail to poll RIC — that is enough to prove the
branch and that `awslambdaric` is installed.

### Commit
`Phase 3: schedule handler + dual-mode entrypoint`

---

## Phase 4 — Infra (single)

### Inputs
Phase 3 (image buildable).

### Ownership
`IMPLEMENTATION_INFRA.md` / `infra/**`.

### Exit-check (Tier 1 + plan)
`cd infra && terraform fmt -check && terraform init -backend=false && terraform validate`.
`terraform plan -var-file=terraform.tfvars.example -out=tfplan` (may need
dummy AWS creds / `-input=false`; if provider cannot auth, still `validate`
and grep the `.tf` for `state = var.enable_schedule ? "ENABLED" : "DISABLED"`
and `schedule_expression_timezone = "America/Chicago"` and
`ignore_changes = [image_uri]`).

Do **not** apply to AWS unless the operator's credentials are available.
If they are: apply with `enable_schedule=false`, then RUNBOOK seed + invoke
(Tier 3, SPEC §7.1). If they are not: document exact manual steps; do not
claim §7.1 done.

### Commit
`Phase 4: terraform schedule plane`

---

## Phase M_final — Polish (orchestrator-driven)

### Inputs
Phases 0–4.

### Does
Root `README.md` section "Daily Telegram recommendations" pointing at
`specs/auto-job-recommendations/` and `infra/RUNBOOK.md`. Root `PLAN.md` /
`SPEC.md` pointers already added with this doc set; confirm they still
match. Disclose SPEC §9 stubs in the README section.

### Exit-check
SPEC §7.1–§7.7: unit tests cover §7.2–§7.4 and §7.7; §7.5 from terraform
grep/plan; §7.6 from compose health; §7.1 live or manual steps listed.

### Commit
`M_final: schedule plane README + verification`

---

## Sub-agent dispatch rules

Each prompt: exact section refs; files owned / forbidden; import
`is_qualified` / schemas rather than redefine; hard rules 1–10; env
gotchas; report files created, commands run, ambiguity hit.

Parallel Phase 1+2: non-overlapping files. `schemas.py` exists from Phase 0.

Review agents: read-only.

## Failure handling

Exit-check fails → orchestrator reproduces, then ≤3 targeted fixes. Never
mark done on self-report. Do not enable `enable_schedule` to "make the
check pass".

## What "done" means

- [ ] Phases 0–4 + M_final exit-checks reproduced by orchestrator
- [ ] `uv run pytest tests/test_schedule_*.py` green
- [ ] `uv run mypy --strict` on `jdparser/schedule`
- [ ] Compose `/api/health` ok
- [ ] Terraform validate; schedule default DISABLED
- [ ] SPEC §7.1 live invoke **or** explicit manual steps (then not claimed)
- [ ] Pins: boto3 1.43.79, awslambdaric 4.0.2 in Dockerfile, aws provider 6.55.0

## Final report format

```
BUILD COMPLETE — Daily job recommendations
Phases: 0, 1+2, 3, 4, M_final  [✅/❌]
Exit-checks reproduced: [list, tier]
SPEC §7.1 live invoke: [PASS/FAIL/NOT RUN — steps]
SPEC §7.6 compose health: [PASS/FAIL]
Known stubs: [SPEC §9]
Commits: [one per phase]
```
