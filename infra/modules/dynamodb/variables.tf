# FILE 6a: infra/modules/dynamodb/variables.tf

variable "env" {
  description = "Deployment environment (dev | prod). Appended to table names."
  type        = string
}

variable "prefix" {
  description = "Project prefix (unused in table names directly, kept for consistency)."
  type        = string
  default     = "ecom-lakehouse"
}

variable "kms_key_arn" {
  description = <<-EOT
    ARN of a CMK for DynamoDB SSE.
    Leave empty in dev — DynamoDB will use the AWS-managed key (aws/dynamodb).
    Required when use_cmk=true (prod).
  EOT
  type        = string
  default     = ""
}

variable "use_cmk" {
  description = <<-EOT
    true  → pass kms_key_arn to server_side_encryption block (prod).
    false → use AWS-managed DynamoDB key (dev — no cost).
    ADR-017: CMKs required in prod for audit trail and cross-account access control.
  EOT
  type        = bool
  default     = false
}
