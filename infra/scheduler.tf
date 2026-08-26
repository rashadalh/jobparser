resource "aws_scheduler_schedule" "daily_0700" {
  name                         = "${var.name_prefix}-daily-0700"
  schedule_expression          = "cron(0 7 * * ? *)"
  schedule_expression_timezone = "America/Chicago"
  # Persist enable_schedule in tfvars. Never one-shot -var.
  state = var.enable_schedule ? "ENABLED" : "DISABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.search.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ scheduled_time = "<aws.scheduler.scheduled-time>" })

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
  statement_id  = "AllowSchedulerInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.search.function_name
  principal     = "scheduler.amazonaws.com"
  source_arn    = aws_scheduler_schedule.daily_0700.arn
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
            "aws:SourceArn" = aws_scheduler_schedule.daily_0700.arn
          }
        }
      }
    ]
  })
}
