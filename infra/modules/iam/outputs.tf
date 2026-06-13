# FILE 8b: infra/modules/iam/outputs.tf

output "normalize_lambda_role_arn" {
  description = "ARN of the normalize Lambda role (also used for validate_schema Lambda)."
  value       = aws_iam_role.normalize_lambda.arn
}

output "normalize_lambda_role_name" {
  description = "Name of the normalize Lambda role."
  value       = aws_iam_role.normalize_lambda.name
}

output "glue_ingest_role_arn" {
  description = "ARN of the Glue ingest/optimize job role."
  value       = aws_iam_role.glue_ingest.arn
}

output "glue_ingest_role_name" {
  description = "Name of the Glue ingest role."
  value       = aws_iam_role.glue_ingest.name
}

output "archive_lambda_role_arn" {
  description = "ARN of the archive Lambda role."
  value       = aws_iam_role.archive_lambda.arn
}

output "archive_lambda_role_name" {
  description = "Name of the archive Lambda role."
  value       = aws_iam_role.archive_lambda.name
}

output "stepfunctions_role_arn" {
  description = "ARN of the Step Functions orchestration role."
  value       = aws_iam_role.stepfunctions.arn
}

output "stepfunctions_role_name" {
  description = "Name of the Step Functions role."
  value       = aws_iam_role.stepfunctions.name
}

output "gha_deploy_role_arn" {
  description = "ARN of the GitHub Actions OIDC deploy role."
  value       = aws_iam_role.gha_deploy.arn
}

output "gha_deploy_role_name" {
  description = "Name of the GitHub Actions deploy role."
  value       = aws_iam_role.gha_deploy.name
}

output "eventbridge_sf_role_arn" {
  description = "ARN of the EventBridge → Step Functions invocation role."
  value       = aws_iam_role.eventbridge_sf.arn
}
