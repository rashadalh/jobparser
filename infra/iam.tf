resource "aws_iam_role" "search_lambda" {
  name = "${var.name_prefix}-search-lambda"

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
    Name = "${var.name_prefix}-search-lambda"
    Role = "search-lambda"
  }
}

resource "aws_iam_role_policy" "search_lambda" {
  name = "${var.name_prefix}-search-lambda"
  role = aws_iam_role.search_lambda.id

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
        Resource = "arn:aws:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${var.name_prefix}-search*"
      },
      {
        Sid    = "S3Objects"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:GetObjectAttributes",
          "s3:HeadObject",
        ]
        Resource = [
          "${aws_s3_bucket.runs.arn}/resume/*",
          "${aws_s3_bucket.runs.arn}/config/*",
          "${aws_s3_bucket.runs.arn}/profiles/*",
          "${aws_s3_bucket.runs.arn}/runs/*",
          "${aws_s3_bucket.runs.arn}/state/*",
        ]
      },
      {
        Sid      = "S3List"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = aws_s3_bucket.runs.arn
        Condition = {
          StringLike = {
            "s3:prefix" = [
              "resume",
              "resume/*",
              "config",
              "config/*",
              "profiles",
              "profiles/*",
              "runs",
              "runs/*",
              "state",
              "state/*",
            ]
          }
        }
      },
      {
        Sid    = "Secrets"
        Effect = "Allow"
        Action = ["secretsmanager:GetSecretValue"]
        Resource = [
          aws_secretsmanager_secret.runtime.arn,
          aws_secretsmanager_secret.telegram_alerts.arn,
        ]
      },
      {
        Sid      = "Dlq"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.search_dlq.arn
      }
    ]
  })
}

resource "aws_iam_role" "scheduler" {
  name = "${var.name_prefix}-scheduler"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "scheduler.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = {
    Name = "${var.name_prefix}-scheduler"
    Role = "scheduler"
  }
}

resource "aws_iam_role_policy" "scheduler" {
  name = "${var.name_prefix}-scheduler"
  role = aws_iam_role.scheduler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "InvokeSearch"
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = aws_lambda_function.search.arn
      }
    ]
  })
}
