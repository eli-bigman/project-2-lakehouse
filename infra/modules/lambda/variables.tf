# FILE 16a: infra/modules/lambda/variables.tf

variable "env" {
  description = "Deployment environment (dev | prod)."
  type        = string
}

variable "prefix" {
  description = "Project prefix for Lambda function naming."
  type        = string
  default     = "ecom-lakehouse"
}

# ── IAM role ARNs ─────────────────────────────────────────────────────────────

variable "normalize_role_arn" {
  description = "IAM role ARN for the normalize Lambda function."
  type        = string
}

variable "claim_role_arn" {
  description = "IAM role ARN for the claim_file Lambda function."
  type        = string
}

variable "archive_role_arn" {
  description = "IAM role ARN for the archive_file Lambda function."
  type        = string
}

variable "validate_schema_role_arn" {
  description = "IAM role ARN for the validate_schema Lambda function (reuses normalize role perms)."
  type        = string
}

# ── DynamoDB table names (injected as env vars into Lambda) ────────────────────

variable "ledger_table_name" {
  description = "Name of the DynamoDB ingestion ledger table."
  type        = string
}

variable "watermarks_table_name" {
  description = "Name of the DynamoDB watermarks table."
  type        = string
}

# ── S3 bucket names (injected as env vars into Lambda) ────────────────────────

variable "raw_bucket" {
  description = "Name of the raw S3 bucket."
  type        = string
}

variable "staging_bucket" {
  description = "Name of the staging S3 bucket."
  type        = string
}

variable "dwh_bucket" {
  description = "Name of the DWH S3 bucket."
  type        = string
}

variable "archive_bucket" {
  description = "Name of the archive S3 bucket."
  type        = string
}

variable "artifacts_bucket" {
  description = "Name of the artifacts bucket (used for Lambda package location reference)."
  type        = string
}
