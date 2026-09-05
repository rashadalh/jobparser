output "search_function_name" {
  description = "Search Lambda function name."
  value       = aws_lambda_function.search.function_name
}

output "search_function_arn" {
  description = "Search Lambda function ARN."
  value       = aws_lambda_function.search.arn
}

output "bucket_name" {
  description = "S3 bucket for resume, config, profiles, runs, and state."
  value       = aws_s3_bucket.runs.bucket
}

output "repository_url" {
  description = "ECR repository URL for the API image (no tag)."
  value       = aws_ecr_repository.api.repository_url
}

output "schedule_state" {
  description = "EventBridge Scheduler state per search tick (ENABLED or DISABLED)."
  value       = { for k, s in aws_scheduler_schedule.search : k => s.state }
}

output "runtime_secret_arn" {
  description = "Secrets Manager ARN for OpenRouter + Adzuna keys (not the payload)."
  value       = aws_secretsmanager_secret.runtime.arn
}

output "telegram_secret_arn" {
  description = "Secrets Manager ARN for Telegram bot token + chat id (not the payload)."
  value       = aws_secretsmanager_secret.telegram_alerts.arn
}

output "dlq_url" {
  description = "Search function / scheduler invoke DLQ URL."
  value       = aws_sqs_queue.search_dlq.url
}
