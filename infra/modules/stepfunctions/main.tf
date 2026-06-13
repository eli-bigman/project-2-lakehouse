# FILE 13: infra/modules/stepfunctions/main.tf
# Step Functions state machine (Standard workflow, ADR-010) + EventBridge trigger.
# ADR-010: Standard type chosen (Express 5-min timeout rules it out for Glue jobs).
# The ASL definition is in asl.tftpl — rendered via templatefile() at plan time.
# EventBridge rule fires when S3 ObjectCreated events arrive on the raw bucket.
# Requires: raw bucket has EventBridge notification enabled (see s3_notification below).

# ─────────────────────────────────────────────────────────────────────────────
# CLOUDWATCH LOG GROUP for execution logs
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "sf_execution" {
  name              = "/aws/states/${var.prefix}-sm-${var.env}"
  retention_in_days = 30

  tags = {
    Name    = "/aws/states/${var.prefix}-sm-${var.env}"
    Purpose = "stepfunctions-execution-logs"
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# STATE MACHINE
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_sfn_state_machine" "pipeline" {
  name     = "${var.prefix}-sm-${var.env}"
  type     = "STANDARD"
  role_arn = var.sf_role_arn

  # ASL rendered from template — substitutes all Lambda/Glue/SNS ARNs at plan time.
  definition = templatefile("${path.module}/asl.tftpl", {
    normalize_fn_arn       = var.normalize_fn_arn
    claim_fn_arn           = var.claim_fn_arn
    archive_fn_arn         = var.archive_fn_arn
    validate_schema_fn_arn = var.validate_schema_fn_arn
    glue_ingest_job        = var.glue_ingest_job_name
    glue_optimize_job      = var.glue_optimize_job_name
    sns_topic_arn          = var.sns_topic_arn
    env                    = var.env
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.sf_execution.arn}:*"
    include_execution_data = true
    level                  = "ERROR"
  }

  tracing_configuration {
    enabled = true
  }

  tags = {
    Name    = "${var.prefix}-sm-${var.env}"
    Purpose = "ingestion-pipeline-orchestrator"
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# S3 → EVENTBRIDGE NOTIFICATION
# Enables EventBridge to receive ObjectCreated events from the raw bucket.
# Without this, EventBridge rules on S3 events are inert.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_s3_bucket_notification" "raw_eventbridge" {
  bucket      = var.raw_bucket_name
  eventbridge = true
}

# ─────────────────────────────────────────────────────────────────────────────
# EVENTBRIDGE RULE: S3 ObjectCreated on raw bucket
# Fires whenever a new file lands in the raw bucket.
# Pattern matches S3 PutObject, CompleteMultipartUpload events.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_event_rule" "s3_raw_trigger" {
  name        = "${var.prefix}-raw-s3-trigger-${var.env}"
  description = "Trigger ingestion pipeline when a file lands in the raw S3 bucket."

  event_pattern = jsonencode({
    source      = ["aws.s3"]
    detail-type = ["Object Created"]
    detail = {
      bucket = {
        name = [var.raw_bucket_name]
      }
      # Only trigger for data file types; exclude _$folder$ and empty keys.
      object = {
        key = [{ prefix = "" }]
      }
    }
  })

  tags = {
    Name    = "${var.prefix}-raw-s3-trigger-${var.env}"
    Purpose = "s3-to-stepfunctions-trigger"
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# EVENTBRIDGE TARGET: Start the state machine
# EventBridge assumes the eventbridge_sf_role to call states:StartExecution.
# Input transformer maps the S3 event to the pipeline's expected input shape.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_event_target" "sf_trigger" {
  rule     = aws_cloudwatch_event_rule.s3_raw_trigger.name
  arn      = aws_sfn_state_machine.pipeline.arn
  role_arn = var.eventbridge_role_arn

  # Transform the S3 event into the pipeline input schema.
  input_transformer {
    input_paths = {
      bucket   = "$.detail.bucket.name"
      key      = "$.detail.object.key"
      event_id = "$.id"
    }

    # Pipeline receives: file_key (S3 key), source_bucket, batch_id (derived from key).
    input_template = <<-JSON
      {
        "file_key": "<key>",
        "source_bucket": "<bucket>",
        "event_id": "<event_id>",
        "dataset": "PLACEHOLDER_PARSED_FROM_KEY"
      }
    JSON
    # Note: dataset is parsed from the S3 key prefix by the claim_file Lambda.
    # e.g., orders/2025/04/orders_apr_2025.xlsx → dataset = "orders"
  }
}
