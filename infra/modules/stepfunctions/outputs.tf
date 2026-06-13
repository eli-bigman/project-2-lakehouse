# FILE 14b: infra/modules/stepfunctions/outputs.tf

output "state_machine_arn" {
  description = "ARN of the Step Functions state machine."
  value       = aws_sfn_state_machine.pipeline.arn
}

output "state_machine_name" {
  description = "Name of the Step Functions state machine."
  value       = aws_sfn_state_machine.pipeline.name
}

output "sf_log_group_name" {
  description = "Name of the CloudWatch log group for SF execution logs."
  value       = aws_cloudwatch_log_group.sf_execution.name
}

output "eventbridge_rule_name" {
  description = "Name of the EventBridge rule that triggers the pipeline."
  value       = aws_cloudwatch_event_rule.s3_raw_trigger.name
}
