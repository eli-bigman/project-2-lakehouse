# FILE 8a: infra/modules/iam/variables.tf

variable "env" {
  description = "Deployment environment (dev | prod)."
  type        = string
}

variable "prefix" {
  description = "Project prefix for resource naming."
  type        = string
  default     = "ecom-lakehouse"
}

variable "account_id" {
  description = "AWS account ID. Used to construct ARNs in trust policies."
  type        = string
}

# ── S3 bucket ARNs ────────────────────────────────────────────────────────────

variable "raw_bucket_arn" {
  description = "ARN of the raw landing S3 bucket."
  type        = string
}

variable "staging_bucket_arn" {
  description = "ARN of the staging/normalized S3 bucket."
  type        = string
}

variable "dwh_bucket_arn" {
  description = "ARN of the curated DWH (Delta Lake) S3 bucket."
  type        = string
}

variable "archive_bucket_arn" {
  description = "ARN of the archive S3 bucket."
  type        = string
}

variable "quarantine_bucket_arn" {
  description = "ARN of the quarantine S3 bucket."
  type        = string
}

variable "artifacts_bucket_arn" {
  description = "ARN of the artifacts S3 bucket (Glue scripts, wheels)."
  type        = string
}

variable "athena_results_bucket_arn" {
  description = "ARN of the Athena query-results S3 bucket."
  type        = string
}

# ── DynamoDB table ARNs ───────────────────────────────────────────────────────

variable "ledger_table_arn" {
  description = "ARN of the ingestion ledger DynamoDB table."
  type        = string
}

variable "watermarks_table_arn" {
  description = "ARN of the watermarks DynamoDB table."
  type        = string
}

# ── Notification & alerting ───────────────────────────────────────────────────

variable "sns_topic_arn" {
  description = "ARN of the SNS alerts topic. Step Functions role needs sns:Publish."
  type        = string
}

# ── GitHub Actions OIDC ───────────────────────────────────────────────────────

variable "github_org" {
  description = "GitHub organisation (or user account) that owns the repository."
  type        = string
  default     = "eli-bigman"
}

variable "github_repo" {
  description = "GitHub repository name (without org prefix)."
  type        = string
  default     = "ecom-lakehouse"
}
