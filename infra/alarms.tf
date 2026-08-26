resource "aws_cloudwatch_metric_alarm" "search_errors" {
  alarm_name          = "${var.name_prefix}-search-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 5
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 60
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Search Lambda reported errors."
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    FunctionName = aws_lambda_function.search.function_name
  }

  tags = {
    Name = "${var.name_prefix}-search-errors"
  }
}

resource "aws_cloudwatch_metric_alarm" "search_dlq" {
  alarm_name          = "${var.name_prefix}-search-dlq"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 60
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Search DLQ has visible messages."
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    QueueName = aws_sqs_queue.search_dlq.name
  }

  tags = {
    Name = "${var.name_prefix}-search-dlq"
  }
}

resource "aws_cloudwatch_metric_alarm" "search_stale" {
  count               = var.enable_schedule ? 1 : 0
  alarm_name          = "${var.name_prefix}-search-stale"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 26
  metric_name         = "Invocations"
  namespace           = "AWS/Lambda"
  period              = 3600
  statistic           = "Sum"
  threshold           = 1
  treat_missing_data  = "breaching"
  alarm_description   = "Search Lambda missed the 07:00 America/Chicago tick (plus slack)."
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    FunctionName = aws_lambda_function.search.function_name
  }

  tags = {
    Name = "${var.name_prefix}-search-stale"
  }
}
