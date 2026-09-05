# Four locations, reserved_concurrency=1 (notified-set mutex). Stagger 16 min
# (Lambda timeout) starting 07:00 America/Chicago so ticks do not throttle.
# Monday = last week; Tuesday–Friday = last 24 hours. No weekend ticks.

locals {
  search_schedules = {
    "mon-week-texas"    = { cron = "cron(0 7 ? * MON *)", search = "texas", max_days_old = 7 }
    "mon-week-new-york" = { cron = "cron(16 7 ? * MON *)", search = "new-york", max_days_old = 7 }
    "mon-week-chicago"  = { cron = "cron(32 7 ? * MON *)", search = "chicago", max_days_old = 7 }
    "mon-week-boston"   = { cron = "cron(48 7 ? * MON *)", search = "boston", max_days_old = 7 }
    "wkday-1d-texas"    = { cron = "cron(0 7 ? * TUE-FRI *)", search = "texas", max_days_old = 1 }
    "wkday-1d-new-york" = { cron = "cron(16 7 ? * TUE-FRI *)", search = "new-york", max_days_old = 1 }
    "wkday-1d-chicago"  = { cron = "cron(32 7 ? * TUE-FRI *)", search = "chicago", max_days_old = 1 }
    "wkday-1d-boston"   = { cron = "cron(48 7 ? * TUE-FRI *)", search = "boston", max_days_old = 1 }
  }
}

resource "aws_scheduler_schedule" "search" {
  for_each = local.search_schedules

  name                         = "${var.name_prefix}-${each.key}"
  schedule_expression          = each.value.cron
  schedule_expression_timezone = "America/Chicago"
  # Persist enable_schedule in tfvars. Never one-shot -var.
  state = var.enable_schedule ? "ENABLED" : "DISABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.search.arn
    role_arn = aws_iam_role.scheduler.arn
    input = jsonencode({
      scheduled_time = "<aws.scheduler.scheduled-time>"
      search         = each.value.search
      max_days_old   = each.value.max_days_old
    })

    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 2
    }

    dead_letter_config {
      arn = aws_sqs_queue.search_dlq.arn
    }
  }
}

resource "aws_lambda_permission" "scheduler" {
  for_each = aws_scheduler_schedule.search

  statement_id  = "AllowSchedulerInvoke-${each.key}"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.search.function_name
  principal     = "scheduler.amazonaws.com"
  source_arn    = each.value.arn
}

resource "aws_sqs_queue_policy" "search_dlq" {
  queue_url = aws_sqs_queue.search_dlq.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowSchedulerSendMessage"
        Effect = "Allow"
        Principal = {
          Service = "scheduler.amazonaws.com"
        }
        Action   = "sqs:SendMessage"
        Resource = aws_sqs_queue.search_dlq.arn
        Condition = {
          ArnEquals = {
            "aws:SourceArn" = [for s in aws_scheduler_schedule.search : s.arn]
          }
        }
      }
    ]
  })
}
