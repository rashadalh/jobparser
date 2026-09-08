# Daily job recommendations — infra RUNBOOK

Operator steps for the Terraform root in this directory. Do not put secret
values in git. Do not `-auto-approve` a fresh plan.

CreateFunction needs a real ECR image digest. `lifecycle.ignore_changes =
[image_uri]` only ignores later tag drift; it does **not** skip the image on
first create. First apply keeps `enable_schedule = false`.

## 1. SSO, init, target ECR, push image, full apply

```bash
export AWS_PROFILE=your-sso-profile
export AWS_REGION=us-east-1

aws sso login --profile "$AWS_PROFILE"

cd /path/to/jdparser/infra
cp terraform.tfvars.example terraform.tfvars
# Set aws_profile if you do not want the env/SSO default chain.
# Leave enable_schedule = false. Never set it true on first apply.

terraform init

# ECR only — so there is a repository to push to before CreateFunction.
terraform plan \
  -var-file=terraform.tfvars \
  -target=aws_ecr_repository.api \
  -target=aws_ecr_lifecycle_policy.api \
  -out=tfplan \
  -input=false
terraform apply tfplan

AWS_PROFILE="$AWS_PROFILE" AWS_REGION="$AWS_REGION" ./scripts/push-api-image.sh latest

# Full stack. Schedule stays DISABLED.
terraform plan -var-file=terraform.tfvars -out=tfplan -input=false
terraform apply tfplan

terraform output
```

`push-api-image.sh` builds `packages/api/Dockerfile` (`linux/arm64`), tags
`{repository_url}:{tag}`, pushes, then `aws lambda update-function-code` **if**
the function already exists. It does not run `terraform apply`.

## 2. Put secret values

Copy the example envelopes (gitignored destinations) and fill real values.
Terraform created empty JSON shells and ignores later `secret_string` drift.

```bash
cd /path/to/jdparser/infra
cp runtime.secret.example.json runtime.secret.json
cp telegram-alerts.secret.example.json telegram-alerts.secret.json
# edit both files — never commit them

aws secretsmanager put-secret-value \
  --secret-id "$(terraform output -raw runtime_secret_arn)" \
  --secret-string file://runtime.secret.json \
  --region "$AWS_REGION" \
  --profile "$AWS_PROFILE"

aws secretsmanager put-secret-value \
  --secret-id "$(terraform output -raw telegram_secret_arn)" \
  --secret-string file://telegram-alerts.secret.json \
  --region "$AWS_REGION" \
  --profile "$AWS_PROFILE"
```

The `telegram-alerts` secret is the one chat for recs and alarms. Recs go out
even when `enable_telegram_alerts` is false; that flag only creates the alarm
relay zip.

## 3. Upload resume

```bash
BUCKET="$(terraform output -raw bucket_name)"
aws s3 cp resume.pdf "s3://${BUCKET}/resume/current" \
  --region "$AWS_REGION" --profile "$AWS_PROFILE"
```

Use the real resume path. Default key is `resume/current` (raw bytes; suffix is
sniffed from magic bytes). A second PDF can live at `resume/{slug}`; pass
`"resume_key":"<slug>"` on invoke. The weekday schedules keep using `current`.

## 4. Optional search config

Missing object → handler defaults (`locations` inferred, `broaden_search=true`,
`max_days_old=7`, `include_agencies=false`). To override:

```bash
# config/search.json example:
# {"locations":["Austin, TX"],"broaden_search":true,"max_days_old":7,"include_agencies":false}

aws s3 cp config/search.json "s3://${BUCKET}/config/search.json" \
  --region "$AWS_REGION" --profile "$AWS_PROFILE"
```

## 5. Manual invoke

Empty payload uses now in `America/Chicago` for `schedule_date` and
`config/search.json` (defaults if missing).

`search` is a lock-key slug. Pass `locations` on the event (operator data;
Terraform already does). Last week is `"max_days_old":7` (also the default when
the event builds a config). Last 24 hours is `"max_days_old":1`. Each `search`
has its own day lock, so you can run more than one the same day:

```bash
FN="$(terraform output -raw search_function_name)"

# (1) Texas, last week
aws lambda invoke --function-name "$FN" --cli-binary-format raw-in-base64-out \
  --cli-read-timeout 900 \
  --payload '{"search":"texas","locations":["Texas"]}' \
  --region "$AWS_REGION" --profile "$AWS_PROFILE" /tmp/out.json && cat /tmp/out.json

# (2) New York, NY, last 24 hours
aws lambda invoke --function-name "$FN" --cli-binary-format raw-in-base64-out \
  --cli-read-timeout 900 \
  --payload '{"search":"new-york","locations":["New York, NY"],"max_days_old":1}' \
  --region "$AWS_REGION" --profile "$AWS_PROFILE" /tmp/out.json && cat /tmp/out.json
```

Expect `ok: true` and `status: completed` (or `skipped` on a same-day repeat
of the **same** `search`). A full graph run can take several minutes.

## 6. Confirm S3 run object + Telegram

```bash
BUCKET="$(terraform output -raw bucket_name)"
aws s3 ls "s3://${BUCKET}/runs/" --recursive \
  --region "$AWS_REGION" --profile "$AWS_PROFILE"
```

A successful tick writes `runs/{schedule_date}/{run_id}.json`. Telegram recs
and alarm relay messages share the chat in the `telegram-alerts` secret
(recs: `jdparser {schedule_date}:…`; alarms: `jdparser alarm:…`). The alarm
relay exists only if `enable_telegram_alerts` was applied later.

Spend is on the archived `run.usage` object:

```bash
aws s3 cp "s3://${BUCKET}/runs/{schedule_date}/{run_id}.json" /tmp/run.json \
  --region "$AWS_REGION" --profile "$AWS_PROFILE"
python3 -c 'import json; print(json.load(open("/tmp/run.json"))["run"].get("usage"))'
```

## 7. Pause / resume the weekday 07:00 schedules

Eight EventBridge schedules (four locations × Monday last-week and
Tue–Fri last-24h). They start at 07:00 America/Chicago and stagger 16 minutes
because reserved concurrency is 1.

Edit **`terraform.tfvars`** (persist the flag):

```hcl
enable_schedule = true   # resume
# enable_schedule = false  # pause
```

Then saved-plan apply (section 8). Never `terraform apply -var='enable_schedule=true'`.
A one-shot `-var` is not in state; the next bare plan uses the default `false`
and **disables** (or destroys the stale alarm).

The stale-invoke alarm is created only while `enable_schedule` is true.

## 8. Saved-plan apply (every change)

```bash
cd /path/to/jdparser/infra
terraform fmt
terraform validate
terraform plan -var-file=terraform.tfvars -out=tfplan -input=false
terraform apply tfplan
```

Do **not** `terraform apply -auto-approve` of a fresh plan. Apply the saved
`tfplan` file only.

Optional later: `enable_telegram_alerts = true` in tfvars (destroys the zip
relay if you flip it back to false). `alarm_email` non-empty adds an SNS email
subscription (confirm the AWS email).
