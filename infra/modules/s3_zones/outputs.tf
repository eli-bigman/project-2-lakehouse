# FILE 4: infra/modules/s3_zones/outputs.tf
# Exports bucket names and ARNs for all 7 zones (4 stateful + 3 stateless).
# Downstream modules consume these to build IAM policies and Glue job arguments.

# ── Stateful zone outputs ──────────────────────────────────────────────────────

output "raw_bucket_name" {
  description = "Name of the raw landing bucket."
  value       = aws_s3_bucket.raw.id
}

output "raw_bucket_arn" {
  description = "ARN of the raw landing bucket."
  value       = aws_s3_bucket.raw.arn
}

output "dwh_bucket_name" {
  description = "Name of the curated DWH (Delta Lake) bucket."
  value       = aws_s3_bucket.dwh.id
}

output "dwh_bucket_arn" {
  description = "ARN of the curated DWH (Delta Lake) bucket."
  value       = aws_s3_bucket.dwh.arn
}

output "archive_bucket_name" {
  description = "Name of the archive bucket (post-ingest originals)."
  value       = aws_s3_bucket.archive.id
}

output "archive_bucket_arn" {
  description = "ARN of the archive bucket."
  value       = aws_s3_bucket.archive.arn
}

output "quarantine_bucket_name" {
  description = "Name of the quarantine bucket (rejected records)."
  value       = aws_s3_bucket.quarantine.id
}

output "quarantine_bucket_arn" {
  description = "ARN of the quarantine bucket."
  value       = aws_s3_bucket.quarantine.arn
}

# ── Stateless zone outputs ─────────────────────────────────────────────────────

output "staging_bucket_name" {
  description = "Name of the staging/normalized bucket (7-day expiry)."
  value       = aws_s3_bucket.stateless["staging"].id
}

output "staging_bucket_arn" {
  description = "ARN of the staging/normalized bucket."
  value       = aws_s3_bucket.stateless["staging"].arn
}

output "athena_results_bucket_name" {
  description = "Name of the Athena query-results bucket (30-day expiry)."
  value       = aws_s3_bucket.stateless["athena-results"].id
}

output "athena_results_bucket_arn" {
  description = "ARN of the Athena query-results bucket."
  value       = aws_s3_bucket.stateless["athena-results"].arn
}

output "artifacts_bucket_name" {
  description = "Name of the artifacts bucket (Glue scripts, JARs, wheels — indefinite)."
  value       = aws_s3_bucket.stateless["artifacts"].id
}

output "artifacts_bucket_arn" {
  description = "ARN of the artifacts bucket."
  value       = aws_s3_bucket.stateless["artifacts"].arn
}
