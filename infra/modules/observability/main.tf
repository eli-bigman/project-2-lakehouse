# FILE 11: infra/modules/observability/main.tf
# SNS alerts, CloudWatch log groups, metric alarms, EventBridge Glue failure rule,
# and a basic CloudWatch dashboard.
#
# dev: SNS uses alias/aws/sns (no CMK cost); prod: CMK via var.kms_key_arn.
#
# NOTE (SNS + managed KMS in dev): When SNS uses alias/aws/sns and CloudWatch or
# Step Functions publishes to the topic, the service must have kms:GenerateDataKey
# + kms:Decrypt on the managed key. Since the aws/sns managed key policy cannot be
# edited, delivery may fail silently in dev. Mitigation options:
#   a) Use a CMK even in dev (add kms_key_arn to dev.tfvars).
#   b) Disable SSE on the dev SNS topic (comment out kms_master_key_id).
#   c) Accept the risk in dev and use CMK in prod.
# This is documented rather than silently worked around.

data "aws_region" "current" {}

locals {
  # Use alias/aws/sns when no CMK is configured (dev).
  sns_kms_key = var.kms_key_arn != "" ? var.kms_key_arn : "alias/aws/sns"
}

# ─────────────────────────────────────────────────────────────────────────────
# SNS TOPIC + SUBSCRIPTION
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_sns_topic" "alerts" {
  name              = "${var.prefix}-alerts-${var.env}"
  kms_master_key_id = local.sns_kms_key

  tags = {
    Name    = "${var.prefix}-alerts-${var.env}"
    Purpose = "pipeline-failure-alerts"
  }
}

resource "aws_sns_topic_subscription" "alert_email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# ─────────────────────────────────────────────────────────────────────────────
# CLOUDWATCH LOG GROUP
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "pipeline" {
  name              = "/ecom-lakehouse/${var.env}/pipeline"
  retention_in_days = 30

  tags = {
    Name    = "/ecom-lakehouse/${var.env}/pipeline"
    Purpose = "pipeline-execution-logs"
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# EVENTBRIDGE RULE: Glue Job State Change → FAILED/TIMEOUT/ERROR
#
# There is no native CloudWatch metric for Glue job failure state.
# The correct mechanism is an EventBridge Rule on the "Glue Job State Change" event.
# This fires only on genuine failures — no false positives on successful runs.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_event_rule" "glue_job_failure" {
  name        = "${var.prefix}-glue-failure-${var.env}"
  description = "Fire when any monitored Glue job enters FAILED, TIMEOUT, or ERROR state."

  event_pattern = jsonencode({
    source      = ["aws.glue"]
    detail-type = ["Glue Job State Change"]
    detail = {
      # Match only the jobs this module is responsible for.
      jobName = var.glue_job_names
      state   = ["FAILED", "TIMEOUT", "ERROR"]
    }
  })

  tags = {
    Name = "${var.prefix}-glue-failure-${var.env}"
  }
}

resource "aws_cloudwatch_event_target" "glue_failure_sns" {
  rule      = aws_cloudwatch_event_rule.glue_job_failure.name
  target_id = "glue-failure-to-sns"
  arn       = aws_sns_topic.alerts.arn

  input_transformer {
    input_paths = {
      job_name   = "$.detail.jobName"
      state      = "$.detail.state"
      job_run_id = "$.detail.jobRunId"
      message    = "$.detail.message"
    }
    input_template = "\"Glue job failure in ${var.env}: Job=<job_name> State=<state> RunId=<job_run_id> Message=<message>\""
  }
}

# SNS topic policy allowing EventBridge to publish.
data "aws_iam_policy_document" "sns_topic_policy" {
  statement {
    sid    = "AllowEventBridgePublish"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }

    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
  }

  statement {
    sid    = "AllowCloudWatchAlarmPublish"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
    }

    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
  }
}

resource "aws_sns_topic_policy" "alerts" {
  arn    = aws_sns_topic.alerts.arn
  policy = data.aws_iam_policy_document.sns_topic_policy.json
}

# ─────────────────────────────────────────────────────────────────────────────
# CLOUDWATCH METRIC ALARM: Step Functions Execution Failures
#
# AWS/States::ExecutionsFailed is a real CloudWatch metric. We use the exact
# state machine ARN (no wildcards) so the dimension resolves correctly.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "stepfunctions_failed" {
  alarm_name          = "${var.prefix}-sf-failed-${var.env}"
  alarm_description   = "State machine ${var.state_machine_name} had at least one failed execution."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 0
  treat_missing_data  = "notBreaching"

  namespace   = "AWS/States"
  metric_name = "ExecutionsFailed"
  statistic   = "Sum"
  period      = 300

  dimensions = {
    # Exact ARN — wildcards cause the alarm to never fire (CloudWatch matches literally).
    StateMachineArn = "arn:aws:states:${data.aws_region.current.region}:${var.account_id}:stateMachine:${var.state_machine_name}"
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  tags = {
    Name = "${var.prefix}-sf-failed-${var.env}"
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# CLOUDWATCH DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_dashboard" "pipeline" {
  dashboard_name = "${var.prefix}-${var.env}"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title   = "Step Functions Executions"
          view    = "timeSeries"
          stacked = false
          metrics = [
            ["AWS/States", "ExecutionsStarted", "StateMachineArn", "arn:aws:states:${data.aws_region.current.region}:${var.account_id}:stateMachine:${var.state_machine_name}", { "stat" : "Sum", "period" : 300, "label" : "Started" }],
            ["AWS/States", "ExecutionsSucceeded", "StateMachineArn", "arn:aws:states:${data.aws_region.current.region}:${var.account_id}:stateMachine:${var.state_machine_name}", { "stat" : "Sum", "period" : 300, "label" : "Succeeded" }],
            ["AWS/States", "ExecutionsFailed", "StateMachineArn", "arn:aws:states:${data.aws_region.current.region}:${var.account_id}:stateMachine:${var.state_machine_name}", { "stat" : "Sum", "period" : 300, "label" : "Failed" }]
          ]
          period = 300
          region = data.aws_region.current.region
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          title   = "Glue Job Run Times"
          view    = "timeSeries"
          stacked = false
          # Glue driver ExecutorRunTime — non-zero means the job ran; use alongside EventBridge for failure detection.
          metrics = [
            for job in var.glue_job_names : ["Glue", "glue.driver.ExecutorRunTime", "JobName", job, "Type", "gauge", { "stat" : "Sum", "period" : 300 }]
          ]
          period = 300
          region = data.aws_region.current.region
        }
      },
      {
        type   = "alarm"
        x      = 0
        y      = 6
        width  = 24
        height = 4
        properties = {
          title = "Pipeline Alarms"
          alarms = [
            "arn:aws:cloudwatch:${data.aws_region.current.region}:${var.account_id}:alarm:${var.prefix}-sf-failed-${var.env}"
          ]
        }
      }
    ]
  })
}
