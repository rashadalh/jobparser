resource "aws_secretsmanager_secret" "runtime" {
  name                    = "${var.name_prefix}/runtime"
  recovery_window_in_days = 7

  tags = {
    Name = "${var.name_prefix}/runtime"
  }
}

resource "aws_secretsmanager_secret_version" "runtime" {
  secret_id = aws_secretsmanager_secret.runtime.id
  secret_string = jsonencode({
    OPENROUTER_API_KEY = ""
    ADZUNA_APP_ID      = ""
    ADZUNA_APP_KEY     = ""
  })

  lifecycle {
    ignore_changes = [secret_string]
  }
}

resource "aws_secretsmanager_secret" "telegram_alerts" {
  name                    = "${var.name_prefix}/telegram-alerts"
  recovery_window_in_days = 7

  tags = {
    Name = "${var.name_prefix}/telegram-alerts"
  }
}

resource "aws_secretsmanager_secret_version" "telegram_alerts" {
  secret_id = aws_secretsmanager_secret.telegram_alerts.id
  secret_string = jsonencode({
    TELEGRAM_BOT_TOKEN = ""
    TELEGRAM_CHAT_ID   = ""
  })

  lifecycle {
    ignore_changes = [secret_string]
  }
}
