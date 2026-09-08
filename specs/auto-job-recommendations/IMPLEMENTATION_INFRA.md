# IMPLEMENTATION_INFRA — Terraform + ECR + Scheduler

> Owns AWS resources and operator docs. References: SPEC §1, §6, §8, §10.
> Shape copied from `prediction_markets/black-scholes-binary/infra` (filename
> prefixes, gated schedule, secret envelopes, `ignore_changes` on image_uri).
> Portable rules: write-terraform-aws skill (one root, no resource subfolders).

## Purpose

Provision the bucket, image repo, search Lambda, 07:00 America/Chicago
schedule (DISABLED by default), secrets shells, DLQ, alarms, optional alarm
relay.

## Files this area owns

Everything under `infra/` listed in SPEC §2. Must NOT edit
`packages/api/jdparser/schedule/*.py` except that `push-api-image.sh` **builds**
`packages/api/Dockerfile`.

## Layout rule

All `.tf` files live in `infra/` (the module directory). Do **not** add
`infra/search/*.tf` without a `module` block — Terraform would ignore them and
the next apply would destroy live addresses.

## Pins

```hcl
# versions.tf
terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.55.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "2.8.0"
    }
  }
}
```

`providers.tf`: `aws` with `region = var.aws_region`,
`profile = var.aws_profile` (empty string = omit profile). `default_tags`
`Project = var.name_prefix`.

Local state (no backend block). Same as binary v1.

## Variables (defaults)

| Name | Type | Default |
|---|---|---|
| `name_prefix` | string | `"jdparser"` |
| `aws_region` | string | `"us-east-1"` |
| `aws_profile` | string | `""` |
| `enable_schedule` | bool | `false` |
| `enable_telegram_alerts` | bool | `false` |
| `lambda_timeout_seconds` | number | `900` |
| `lambda_memory_mb` | number | `3008` |
| `lambda_ephemeral_mb` | number | `2048` |
| `lambda_reserved_concurrency` | number | `1` |
| `api_image_tag` | string | `"latest"` |
| `log_retention_days` | number | `30` |
| `alarm_email` | string | `""` |
| `bucket_name` | string | `null` → `{name_prefix}-runs-{account_id}-{region}` |

`validation`: `lambda_timeout_seconds` is `900`. `enable_schedule` has no
extra validation. Persist flags in gitignored `terraform.tfvars`.

`terraform.tfvars.example` lists every variable with the defaults above and
comments: never set `enable_schedule=true` on first apply.

## Resources (normative names)

Prefix = `var.name_prefix` (default `jdparser`).

### ECR — `ecr.tf`

`aws_ecr_repository.api` name `{prefix}-api`. Scan on push. Lifecycle keep
last 10 images. `force_delete = false`.

### S3 — `s3.tf`

Bucket: versioning on, public access block all four true, ownership
BucketOwnerEnforced, SSE-S3 + bucket key, deny `aws:SecureTransport=false`,
lifecycle abort incomplete multipart 7 days, **no** IA/Glacier. `lifecycle {
prevent_destroy = true }` on the bucket.

### Secrets — `secrets.tf`

Two `aws_secretsmanager_secret` resources: `{prefix}/runtime`,
`{prefix}/telegram-alerts`. `recovery_window_in_days = 7`.
`aws_secretsmanager_secret_version` with a JSON **placeholder** (`{"OPENROUTER_API_KEY":"","ADZUNA_APP_ID":"","ADZUNA_APP_KEY":""}`
and `{"TELEGRAM_BOT_TOKEN":"","TELEGRAM_CHAT_ID":""}`) and

```
lifecycle { ignore_changes = [secret_string] }
```

Commit `infra/runtime.secret.example.json` and
`infra/telegram-alerts.secret.example.json` (same shapes, empty strings).

### IAM — `iam.tf`

Execution role `{prefix}-search-lambda`, principal `lambda.amazonaws.com`.
Inline policy Sids:

- `Logs` — `logs:CreateLogStream`, `logs:PutLogEvents` on
  `arn:aws:logs:{region}:{account}:log-group:/aws/lambda/{prefix}-search*`
- `S3Objects` — `GetObject`, `PutObject`, `GetObjectAttributes`, `HeadObject`
  on `{bucket_arn}/resume/*`, `/config/*`, `/profiles/*`, `/runs/*`, `/state/*`
- `S3List` — `ListBucket` on bucket ARN, condition
  `s3:prefix` `StringLike` **both** the prefix and the prefix with wildcard:
  `resume`, `resume/*`, `config`, `config/*`, `profiles`, `profiles/*`,
  `runs`, `runs/*`, `state`, `state/*`
- `Secrets` — `secretsmanager:GetSecretValue` on the two secret ARNs only
- `Dlq` — `sqs:SendMessage` on the search DLQ

Scheduler role `{prefix}-scheduler`: `lambda:InvokeFunction` on the search
function ARN only. Trust `scheduler.amazonaws.com`.

### Lambda — `lambda.tf`

Log group `/aws/lambda/{prefix}-search` retention `var.log_retention_days`,
`depends_on` from the function.

SQS DLQ `{prefix}-search-dlq` retention `1209600`.

```
package_type  = "Image"
image_uri     = "${aws_ecr_repository.api.repository_url}:${var.api_image_tag}"
architectures = ["arm64"]
timeout       = var.lambda_timeout_seconds
memory_size   = var.lambda_memory_mb
ephemeral_storage { size = var.lambda_ephemeral_mb }
reserved_concurrent_executions = var.lambda_reserved_concurrency
dead_letter_config { target_arn = dlq.arn }
lifecycle { ignore_changes = [image_uri] }
```

**Omit** the `image_config` block. `ignore_changes` does **not** skip the
need for a real image on **CreateFunction** — RUNBOOK target-then-push is
mandatory.

`aws_lambda_function_event_invoke_config` on the search function:
`maximum_retry_attempts = 0` (SPEC `LAMBDA_ASYNC_RETRIES`). Handler exceptions
are not retried by Lambda; they go to the function DLQ. Scheduler
`retry_policy` retries **invoke** failures only (IAM, throttle, service),
not exceptions inside a started invoke.

Env: `SCHEDULE_BUCKET`, `RUNTIME_SECRET_ARN`, `TELEGRAM_SECRET_ARN`,
`JDPARSER_DATA_DIR=/tmp/jdparser-data`.

### Scheduler — `scheduler.tf`

Eight `aws_scheduler_schedule.search` ticks (`for_each`), timezone
`America/Chicago`, `state = var.enable_schedule ? "ENABLED" : "DISABLED"`:

- Monday last week: `cron({0,16,32,48} 7 ? * MON *)` for texas / new-york /
  chicago / boston, payload `search` + `locations` + `max_days_old=7`
- Tuesday–Friday last 24h: `cron({0,16,32,48} 7 ? * TUE-FRI *)`, same
  locations, `max_days_old=1`

16-minute stagger matches `LAMBDA_TIMEOUT_S` so reserved concurrency 1 does
not throttle. No weekend ticks. Cities, 07:00, and the stagger live in this
file (operator data), not in Python.

```
flexible_time_window { mode = "OFF" }
target {
  arn      = aws_lambda_function.search.arn
  role_arn = aws_iam_role.scheduler.arn
  input    = jsonencode({
    scheduled_time = "<aws.scheduler.scheduled-time>"
    search         = ...
    locations      = ...
    max_days_old   = ...
  })
  retry_policy {
    maximum_event_age_in_seconds = 3600    # SCHEDULER_EVENT_AGE_S
    maximum_retry_attempts       = 2       # SCHEDULER_RETRY_ATTEMPTS (extra after first)
  }
  dead_letter_config { arn = aws_sqs_queue.search_dlq.arn }
}
```

`aws_lambda_permission` per schedule (`for_each`) for `scheduler.amazonaws.com`.

SQS policy allowing EventBridge **Scheduler** service to `SendMessage` to the
DLQ with `aws:SourceArn` = the eight schedule ARNs. (Service principal
`scheduler.amazonaws.com`.)

### Alarms — `alarms.tf`

- `{prefix}-search-errors`: `AWS/Lambda` Errors > 0 in 5 minutes (`Period=60`,
  `EvaluationPeriods=5`, `Statistic=Sum`), function name
- `{prefix}-search-dlq`: SQS `ApproximateNumberOfMessagesVisible` > 0 on DLQ
- `{prefix}-search-stale`: Lambda `Invocations` `Statistic=Sum`, `Period=3600`,
  `EvaluationPeriods=80`, `Threshold=1`, `LessThanThreshold`,
  `treat_missing_data = "breaching"` (a never-invoked function emits no
  datapoints; default `missing` would stay INSUFFICIENT_DATA). **`count = 1`
  only when `var.enable_schedule`**. 80 h covers the Friday–Monday gap; a
  missed midweek tick is the error/DLQ alarms.

All alarm actions = SNS topic `{prefix}-alerts` (always created).

One SQS queue `{prefix}-search-dlq` is the **function** DLQ (handler raise /
timeout after accept). Scheduler `dead_letter_config` on the same queue is
**invoke** failures (permission, throttle). Payloads differ (Lambda vs
Scheduler event); operators read the body. Do not add a second queue in v1.

### Optional relay — `sns.tf` + `telegram_relay.tf`

SNS topic `{prefix}-alerts`. Optional email subscription if
`var.alarm_email != ""`.

When `enable_telegram_alerts`: zip `infra/lambda/telegram_relay/handler.py` as
the **zip root** file `handler.py`. Terraform `handler = "handler.handler"`,
runtime `python3.11`, arch `arm64`, timeout 30s, env `TELEGRAM_SECRET_ARN`.
Stdlib `json` + `urllib.request`; boto3 is the **managed runtime** copy, not
the search-image pin. Format CloudWatch alarm JSON; prefix `jdparser alarm:`.
Permission: SNS invoke. Subscription: topic → relay. Relay IAM: logs +
`GetSecretValue` on telegram secret only.

When the flag is false, `count = 0` those resources (destroys function +
subscription if they existed). Persist the flag in tfvars. MVP leaves it
false (SPEC §9).

## Outputs

`search_function_name`, `search_function_arn`, `bucket_name`,
`repository_url`, `schedule_state`, `runtime_secret_arn`,
`telegram_secret_arn`, `dlq_url` — all `-raw` usable.

## Scripts

`infra/scripts/push-api-image.sh`: `docker build --platform linux/arm64` of
`packages/api/Dockerfile`, tag `{repository_url}:{tag}`, `docker push`, then
`aws lambda update-function-code --image-uri` if the function exists.
Requires `AWS_PROFILE` / `AWS_REGION`. Do not `-auto-approve` terraform here.

## RUNBOOK.md (required sections)

1. SSO login, `terraform init`, `-target` ECR, push image, full apply
   (`enable_schedule=false`).
2. `put-secret-value` for both secrets (file:// from gitignored copies).
3. `aws s3 cp resume.pdf s3://$BUCKET/resume/current`
4. Optional `config/search.json`
5. `aws lambda invoke --function-name … /tmp/out.json && cat /tmp/out.json`
6. Confirm S3 run object + Telegram.
7. Pause/resume: `enable_schedule` in **tfvars**, apply. Never one-shot `-var`.
8. `terraform fmt` → `validate` → `terraform plan -out=tfplan` →
   `terraform apply tfplan`. No `-auto-approve` of a fresh plan.

## Done when

`terraform fmt -check` + `terraform validate` in `infra/` (`init -backend=false`
ok). Grep `.tf` for DISABLED schedule, `America/Chicago`, `ignore_changes` on
`image_uri`, and `maximum_retry_attempts = 0` on
`aws_lambda_function_event_invoke_config`. Create of the Lambda **requires** a
pushed ECR image (`ignore_changes` does not help CreateFunction). Orchestrator
does not `apply` unless operator creds exist; then SPEC §7.5 is a plan grep
for `DISABLED`. If no creds: document RUNBOOK steps; do not claim §7.1.
