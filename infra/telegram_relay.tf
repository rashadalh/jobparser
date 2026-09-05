data "archive_file" "telegram_relay" {
  count       = var.enable_telegram_alerts ? 1 : 0
  type        = "zip"
  source_file = "${path.module}/lambda/telegram_relay/handler.py"
  output_path = "${path.module}/lambda/telegram_relay/handler.zip"
}

resource "aws_cloudwatch_log_group" "telegram_relay" {
  count             = var.enable_telegram_alerts ? 1 : 0
  name              = "/aws/lambda/${var.name_prefix}-telegram-relay"
  retention_in_days = var.log_retention_days

  tags = {
    Name = "/aws/lambda/${var.name_prefix}-telegram-relay"
  }
}

resource "aws_iam_role" "telegram_relay" {
  count = var.enable_telegram_alerts ? 1 : 0
  name  = "${var.name_prefix}-telegram-relay"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = {
    Name = "${var.name_prefix}-telegram-relay"
    Role = "telegram-relay"
  }
}

resource "aws_iam_role_policy" "telegram_relay" {
  count = var.enable_telegram_alerts ? 1 : 0
  name  = "${var.name_prefix}-telegram-relay"
  role  = aws_iam_role.telegram_relay[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "Logs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "arn:aws:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${var.name_prefix}-telegram-relay*"
      },
      {
        Sid      = "Secrets"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = aws_secretsmanager_secret.telegram_alerts.arn
      }
    ]
  })
}

resource "aws_lambda_function" "telegram_relay" {
  count            = var.enable_telegram_alerts ? 1 : 0
  function_name    = "${var.name_prefix}-telegram-relay"
  role             = aws_iam_role.telegram_relay[0].arn
  filename         = data.archive_file.telegram_relay[0].output_path
  source_code_hash = data.archive_file.telegram_relay[0].output_base64sha256
  handler          = "handler.handler"
  runtime          = "python3.11"
  architectures    = ["arm64"]
  timeout          = 30

  environment {
    variables = {
      TELEGRAM_SECRET_ARN = aws_secretsmanager_secret.telegram_alerts.arn
    }
  }

  tags = {
    Name = "${var.name_prefix}-telegram-relay"
  }

  depends_on = [aws_cloudwatch_log_group.telegram_relay]
}

resource "aws_lambda_permission" "sns_telegram_relay" {
  count         = var.enable_telegram_alerts ? 1 : 0
  statement_id  = "AllowSnsInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.telegram_relay[0].function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.alerts.arn
}

resource "aws_sns_topic_subscription" "telegram_relay" {
  count     = var.enable_telegram_alerts ? 1 : 0
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.telegram_relay[0].arn
}
