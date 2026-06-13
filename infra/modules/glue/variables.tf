# FILE 10a: infra/modules/glue/variables.tf

variable "env" {
  description = "Deployment environment (dev | prod)."
  type        = string
}

variable "prefix" {
  description = "Project prefix for resource naming."
  type        = string
  default     = "ecom-lakehouse"
}

variable "dwh_bucket_name" {
  description = "Name of the DWH (Delta Lake) S3 bucket — used as table storage location."
  type        = string
}

variable "artifacts_bucket_name" {
  description = "Name of the artifacts bucket — Glue scripts, wheels, temp files."
  type        = string
}

variable "glue_role_arn" {
  description = "ARN of the IAM role that Glue jobs assume during execution."
  type        = string
}

variable "database_name" {
  description = <<-EOT
    Glue Catalog database name. Defaults to the standard naming convention.
    Override only for cross-account catalog scenarios.
  EOT
  type        = string
  default     = ""
}
