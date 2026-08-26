resource "aws_cloudwatch_log_group" "search" {
  name              = "/aws/lambda/${var.name_prefix}-search"
  retention_in_days = var.log_retention_days

  tags = {
    Name = "/aws/lambda/${var.name_prefix}-search"
  }
}

resource "aws_sqs_queue" "search_dlq" {
  name                      = "${var.name_prefix}-search-dlq"
  message_retention_seconds = 1209600

  tags = {
    Name = "${var.name_prefix}-search-dlq"
  }
}

resource "aws_lambda_function" "search" {
  function_name = "${var.name_prefix}-search"
  role          = aws_iam_role.search_lambda.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.api.repository_url}:${var.api_image_tag}"
  architectures = ["arm64"]
  timeout       = var.lambda_timeout_seconds
  memory_size   = var.lambda_memory_mb

  ephemeral_storage {
    size = var.lambda_ephemeral_mb
  }

  reserved_concurrent_executions = var.lambda_reserved_concurrency

  dead_letter_config {
    target_arn = aws_sqs_queue.search_dlq.arn
  }

  environment {
    variables = {
      SCHEDULE_BUCKET     = aws_s3_bucket.runs.bucket
      RUNTIME_SECRET_ARN  = aws_secretsmanager_secret.runtime.arn
      TELEGRAM_SECRET_ARN = aws_secretsmanager_secret.telegram_alerts.arn
      JDPARSER_DATA_DIR   = "/tmp/jdparser-data"
    }
  }

  tags = {
    Name = "${var.name_prefix}-search"
  }

  lifecycle {
    ignore_changes = [image_uri]
  }

  depends_on = [aws_cloudwatch_log_group.search]
}

resource "aws_lambda_function_event_invoke_config" "search" {
  function_name          = aws_lambda_function.search.function_name
  maximum_retry_attempts = 0
}
