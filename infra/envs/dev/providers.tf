# FILE 21: infra/envs/dev/providers.tf
# Provider configuration for the dev environment.
# AWS profile: "personal" (account 647594457599, us-east-1).
# default_tags: applied to every resource created by this provider,
#               satisfying the project-wide tagging requirement.

terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }

    # archive provider: used by the lambda module to create placeholder ZIPs.
    archive = {
      source  = "hashicorp/archive"
      version = ">= 2.4"
    }
  }
}

provider "aws" {
  region  = "eu-west-1"
  profile = "sandbox-lakehouse-dev"

  # Default tags applied to all resources — satisfies tagging ADR.
  default_tags {
    tags = {
      Project     = "ecom-lakehouse"
      Environment = "dev"
      ManagedBy   = "terraform"
    }
  }
}
