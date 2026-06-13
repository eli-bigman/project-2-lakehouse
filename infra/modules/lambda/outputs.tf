# FILE 16b: infra/modules/lambda/outputs.tf

output "normalize_function_arn" {
  description = "ARN of the normalize Lambda function."
  value       = aws_lambda_function.normalize.arn
}

output "normalize_function_name" {
  description = "Name of the normalize Lambda function."
  value       = aws_lambda_function.normalize.function_name
}

output "claim_file_function_arn" {
  description = "ARN of the claim_file Lambda function."
  value       = aws_lambda_function.claim_file.arn
}

output "claim_file_function_name" {
  description = "Name of the claim_file Lambda function."
  value       = aws_lambda_function.claim_file.function_name
}

output "archive_file_function_arn" {
  description = "ARN of the archive_file Lambda function."
  value       = aws_lambda_function.archive_file.arn
}

output "archive_file_function_name" {
  description = "Name of the archive_file Lambda function."
  value       = aws_lambda_function.archive_file.function_name
}

output "validate_schema_function_arn" {
  description = "ARN of the validate_schema Lambda function."
  value       = aws_lambda_function.validate_schema.arn
}

output "validate_schema_function_name" {
  description = "Name of the validate_schema Lambda function."
  value       = aws_lambda_function.validate_schema.function_name
}
