# FILE 1: infra/modules/s3_zones/variables.tf
# Variables for the s3_zones module — controls naming, encryption and lifecycle protection.

variable "env" {
  description = "Deployment environment (dev | prod). Appended to every resource name."
  type        = string
}

variable "prefix" {
  description = "Project-wide resource prefix. Change only if forking the project."
  type        = string
  default     = "ecom-lakehouse"
}

variable "kms_key_arn" {
  description = <<-EOT
    ARN of a customer-managed KMS key (CMK).
    Set use_cmk=true and supply this ARN in prod.
    Leave empty in dev — the AWS-managed key (aws/s3) is used instead.
  EOT
  type        = string
  default     = ""
}

variable "use_cmk" {
  description = <<-EOT
    true  → use the CMK supplied in kms_key_arn (prod).
    false → use the AWS-managed S3 key aws/s3 (dev — no cost).
    ADR-017: dev avoids CMK per-key charges; prod requires CMKs for audit trail.
  EOT
  type        = bool
  default     = false
}

variable "protect_stateful" {
  description = <<-EOT
    Controls force_destroy on stateful S3 buckets (raw, dwh, archive, quarantine).

    true  (default) → S3 will refuse destroy if the bucket is non-empty.
                      Terraform lifecycle prevent_destroy=true is ALSO hardcoded,
                      meaning `terraform destroy` will error regardless of this flag.
    false           → force_destroy is set so S3 empties the bucket on destroy.
                      HOWEVER, the hardcoded prevent_destroy=true in the lifecycle
                      block still prevents `terraform destroy` from completing.

    TEARDOWN PROCEDURE (when you really need to destroy stateful buckets):
      1. Temporarily comment out the `lifecycle { prevent_destroy = true }` blocks
         in stateful.tf (or use `terraform state rm` to untrack the resource).
      2. Set protect_stateful=false via -var or tfvars.
      3. Run: terraform destroy -target=<resource>
    ADR-018: stateful buckets are explicit resources with prevent_destroy protection.
  EOT
  type        = bool
  default     = true
}
