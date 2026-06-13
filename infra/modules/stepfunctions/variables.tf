# FILE 14a: infra/modules/stepfunctions/variables.tf

variable "env" {
  description = "Deployment environment (dev | prod)."
  type        = string
}

variable "prefix" {
  description = "Project prefix for resource naming."
  type        = string
  default     = "ecom-lakehouse"
}

variable "sf_role_arn" {
  description = "IAM role ARN for the Step Functions state machine."
  type        = string
}

variable "eventbridge_role_arn" {
  description = "IAM role ARN for EventBridge to start the state machine."
  type        = string
}

# ── Lambda function ARNs ───────────────────────────────────────────────────────

variable "normalize_fn_arn" {
  description = "ARN of the normalize Lambda function."
  type        = string
}

variable "claim_fn_arn" {
  description = "ARN of the claim_file Lambda function."
  type        = string
}

variable "archive_fn_arn" {
  description = "ARN of the archive_file Lambda function."
  type        = string
}

variable "validate_schema_fn_arn" {
  description = "ARN of the validate_schema Lambda function."
  type        = string
}

# ── Glue job names ─────────────────────────────────────────────────────────────

variable "glue_ingest_job_name" {
  description = "Name of the Glue ingest Spark job."
  type        = string
}

variable "glue_optimize_job_name" {
  description = "Name of the Glue optimize Spark job."
  type        = string
}

# ── Notification ───────────────────────────────────────────────────────────────

variable "sns_topic_arn" {
  description = "ARN of the SNS alerts topic for pipeline failure notifications."
  type        = string
}

# ── S3 trigger ────────────────────────────────────────────────────────────────

variable "raw_bucket_name" {
  description = "Name of the raw S3 bucket (used for EventBridge rule filter)."
  type        = string
}

variable "raw_bucket_arn" {
  description = "ARN of the raw S3 bucket (used for S3 EventBridge notification)."
  type        = string
}
