data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  bucket_name = coalesce(var.bucket_name, "${var.name_prefix}-runs-${data.aws_caller_identity.current.account_id}-${data.aws_region.current.region}")
}

provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile == "" ? null : var.aws_profile

  default_tags {
    tags = {
      Project = var.name_prefix
    }
  }
}
