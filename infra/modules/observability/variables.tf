# FILE 12a: infra/modules/observability/variables.tf

variable "env" {
  description = "Deployment environment (dev | prod)."
  type        = string
}

variable "prefix" {
  description = "Project prefix for resource naming."
  type        = string
  default     = "ecom-lakehouse"
}

variable "alert_email" {
  description = "Email address to receive SNS pipeline failure alerts."
  type        = string
}

variable "glue_job_names" {
  description = <<-EOT
    List of Glue job names to monitor for failures.
    Pass as constructed string locals in dev/main.tf to avoid module dependency cycles.
  EOT
  type        = list(string)
}

variable "state_machine_name" {
  description = <<-EOT
    Name of the Step Functions state machine to monitor.
    Pass as a constructed string local in dev/main.tf to avoid module dependency cycles.
  EOT
  type        = string
}

variable "kms_key_arn" {
  description = "KMS key ARN for SNS topic encryption. Empty = use alias/aws/sns (dev)."
  type        = string
  default     = ""
}

variable "account_id" {
  description = "AWS account ID. Used to build exact CloudWatch alarm dimension ARNs."
  type        = string
}
