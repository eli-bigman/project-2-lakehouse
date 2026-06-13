# FILE 6b: infra/modules/dynamodb/outputs.tf

output "ledger_table_name" {
  description = "Name of the ingestion ledger DynamoDB table."
  value       = aws_dynamodb_table.ingestion_ledger.name
}

output "ledger_table_arn" {
  description = "ARN of the ingestion ledger DynamoDB table."
  value       = aws_dynamodb_table.ingestion_ledger.arn
}

output "watermarks_table_name" {
  description = "Name of the watermarks DynamoDB table."
  value       = aws_dynamodb_table.watermarks.name
}

output "watermarks_table_arn" {
  description = "ARN of the watermarks DynamoDB table."
  value       = aws_dynamodb_table.watermarks.arn
}
