# FILE 19: infra/envs/dev/variables.tf

variable "account_id" {
  description = "AWS account ID (647594457599). Used to construct ARNs in IAM policies."
  type        = string
}

variable "alert_email" {
  description = "Email address to subscribe to the SNS alerts topic."
  type        = string
}

variable "github_org" {
  description = "GitHub organisation for the OIDC deploy role trust policy."
  type        = string
  default     = "eli-bigman"
}

variable "github_repo" {
  description = "GitHub repository name for the OIDC deploy role trust policy."
  type        = string
  default     = "project-2-lakehouse"
}

variable "protect_stateful" {
  description = <<-EOT
    Controls force_destroy on stateful S3 buckets.
    true  (default) → buckets cannot be emptied by Terraform on destroy.
    false → force_destroy enabled; see TEARDOWN PROCEDURE in s3_zones/variables.tf.
  EOT
  type        = bool
  default     = true
}
