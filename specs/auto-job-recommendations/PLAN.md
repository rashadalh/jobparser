# PLAN — Daily job recommendations

> Audience: humans orienting on this plane. One page. Stable across phases.
> Product contract for the browser app stays in `/PLAN.md` / `/SPEC.md`. This
> plane does not replace that app.

## Mission

Once a day at 07:00 America/Chicago, AWS runs the existing LangGraph matcher
against a stored resume. The run JSON is written to S3. New qualified jobs are
sent to Telegram. The operator does not open the browser for this path.

The matcher's display gate does not change: a job is recommended only if the
full JD was extracted and `is_qualified()` (root SPEC §7) is true. Telegram
never sees `uncertain`, `failed`, or screened-out jobs.

## In scope

- EventBridge Scheduler, 07:00 `America/Chicago`, feature-flagged **DISABLED**
  until an operator enables it.
- One container Lambda: the existing `packages/api` image (Playwright, Xvfb,
  graph). Not the Next.js app. Not FastAPI on the scheduled path.
- S3 archive: resume bytes, search config, profile JSON, per-day run JSON,
  notified-job set.
- Telegram push of **new** qualified jobs (and a zero-new heartbeat).
- CloudWatch alarms → SNS → a stdlib Telegram relay (optional flag).
- Dual-mode image: compose still runs `uvicorn`; Lambda detects
  `AWS_LAMBDA_RUNTIME_API` and runs the handler.

## Out of scope

- Deploying or changing the Next.js frontend.
- API Gateway, webhooks, or Telegram **commands** (`/run`, `/status`).
- Auth / multi-user / extra Telegram chats.
- A SQL database; DynamoDB; EFS.
- A second job source or a country other than `ADZUNA_COUNTRY`.
- Re-judging yesterday's jobs; applying to jobs.
- Enabling the daily schedule in the same apply that first creates it
  (`enable_schedule` stays false until a later, deliberate apply).

## Major work surfaces (one IMPLEMENTATION doc each)

| Surface | Doc | Owns |
|---|---|---|
| Handler | `IMPLEMENTATION_HANDLER.md` | Lambda entry, dual-mode image, graph invoke, day lock |
| Store | `IMPLEMENTATION_STORE.md` | S3 keys, resume/config/profiles/runs/notified lifecycle |
| Notify | `IMPLEMENTATION_NOTIFY.md` | Telegram recs, chunking, notified-set updates |
| Infra | `IMPLEMENTATION_INFRA.md` | Terraform, ECR, Scheduler, IAM, secrets, alarms, relay |

## Definition of done

SPEC §7.1–§7.7, in that numbering. Short form: seed resume + secrets; one
invoke archives a `RunRecord` and Telegrams recs or the heartbeat; a same-day
second invoke skips; already-notified jobs are not re-sent; Telegram ⊆
`is_qualified()`; Scheduler stays DISABLED until tfvars; compose health still
works; missing resume fails the lock and sends no recs.

## Source-of-truth ordering

This plane's `SPEC.md` > this plane's `IMPLEMENTATION_*.md` > agent judgment.
Root `/SPEC.md` remains canonical for the graph, schemas, and `is_qualified()`.
Where this SPEC and root SPEC disagree on **this plane** (S3 vs local JSON,
push vs poll), this SPEC wins. Graph behavior is never forked.
