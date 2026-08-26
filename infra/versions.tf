terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.55.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "2.8.0"
    }
  }
}
