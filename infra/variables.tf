variable "name_prefix" {
  type        = string
  description = "Prefix for resource names (SPEC NAME_PREFIX)."
  default     = "jdparser"
}

variable "aws_region" {
  type        = string
  description = "AWS region for this root."
  default     = "us-east-1"
}

variable "aws_profile" {
  type        = string
  description = "Named AWS profile. Empty string omits profile (use env/SSO default chain)."
  default     = ""
}

variable "enable_schedule" {
  type        = bool
  description = "EventBridge Scheduler state. Persist in terraform.tfvars. Default DISABLED."
  default     = false
}

variable "enable_telegram_alerts" {
  type        = bool
  description = "Optional SNS → zip Lambda Telegram alarm relay. Persist in terraform.tfvars."
  default     = false
}

variable "lambda_timeout_seconds" {
  type        = number
  description = "Search Lambda timeout. Must equal SPEC LAMBDA_TIMEOUT_S (900)."
  default     = 900

  validation {
    condition     = var.lambda_timeout_seconds == 900
    error_message = "lambda_timeout_seconds must be 900 (SPEC LAMBDA_TIMEOUT_S)."
  }
}

variable "lambda_memory_mb" {
  type        = number
  description = "Search Lambda memory_size (SPEC LAMBDA_MEMORY_MB)."
  default     = 3008
}

variable "lambda_ephemeral_mb" {
  type        = number
  description = "Search Lambda ephemeral /tmp MB (SPEC LAMBDA_EPHEMERAL_MB)."
  default     = 2048
}

variable "lambda_reserved_concurrency" {
  type        = number
  description = "Search Lambda reserved concurrency (SPEC LAMBDA_RESERVED_CONCURRENCY)."
  default     = 1
}

variable "api_image_tag" {
  type        = string
  description = "ECR tag used on first CreateFunction. Later digests are ignore_changes."
  default     = "latest"
}

variable "log_retention_days" {
  type        = number
  description = "CloudWatch log retention (SPEC LOG_RETENTION_DAYS)."
  default     = 30
}

variable "alarm_email" {
  type        = string
  description = "Optional SNS email subscription. Empty string creates none."
  default     = ""
}

variable "bucket_name" {
  type        = string
  description = "S3 bucket name. Null → {name_prefix}-runs-{account_id}-{region}."
  default     = null
}
