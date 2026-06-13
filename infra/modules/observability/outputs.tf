# FILE 12b: infra/modules/observability/outputs.tf

output "sns_topic_arn" {
  description = "ARN of the SNS alerts topic."
  value       = aws_sns_topic.alerts.arn
}

output "sns_topic_name" {
  description = "Name of the SNS alerts topic."
  value       = aws_sns_topic.alerts.name
}

output "log_group_name" {
  description = "Name of the main pipeline CloudWatch log group."
  value       = aws_cloudwatch_log_group.pipeline.name
}

output "log_group_arn" {
  description = "ARN of the main pipeline CloudWatch log group."
  value       = aws_cloudwatch_log_group.pipeline.arn
}

output "dashboard_name" {
  description = "Name of the CloudWatch dashboard."
  value       = aws_cloudwatch_dashboard.pipeline.dashboard_name
}
