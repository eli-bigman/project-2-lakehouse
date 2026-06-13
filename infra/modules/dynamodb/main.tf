# FILE 5: infra/modules/dynamodb/main.tf
# DynamoDB control-plane tables (ADR-006).
# Two tables: ingestion_ledger (idempotency) and watermarks (last-batch tracking).
# Both use PAY_PER_REQUEST billing — traffic is bursty at file-ingest time, not steady.
# PITR enabled on both for operational safety.

# ─────────────────────────────────────────────────────────────────────────────
# INGESTION LEDGER
# Purpose: one row per file ingested; used for idempotency checks.
# PK: file_key (S3 object key, unique per file drop).
# GSI: dataset-status-index → enables querying all files in a given state.
# TTL: optional; set to epoch 0 to disable per-record TTL, or a future timestamp
#      to auto-expire old ledger entries (operator sets per-item attribute).
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_dynamodb_table" "ingestion_ledger" {
  name         = "ecom_lakehouse_ingestion_ledger_${var.env}"
  billing_mode = "PAY_PER_REQUEST"
  # hash_key is deprecated in AWS provider v6 in favour of key_schema inside the
  # resource body, but the replacement block syntax is not yet supported in the
  # hashicorp/aws Terraform resource (only in the underlying SDK). Keep hash_key
  # for now — it still works and validate passes; upgrade when provider support lands.
  hash_key = "file_key"

  attribute {
    name = "file_key"
    type = "S"
  }

  # GSI key attributes must be declared at table level even if only used in GSI.
  attribute {
    name = "dataset"
    type = "S"
  }

  attribute {
    name = "status"
    type = "S"
  }

  # GSI: allows Step Functions / monitoring to query by dataset+status
  # e.g. "all FAILED files for dataset=orders"
  global_secondary_index {
    name            = "dataset-status-index"
    hash_key        = "dataset"
    range_key       = "status"
    projection_type = "ALL"
  }

  # TTL: items can self-expire by setting the ttl attribute to a Unix epoch.
  # Items without the attribute (or with ttl=0) are never expired.
  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  # Point-in-time recovery: allows restore to any second in the last 35 days.
  point_in_time_recovery {
    enabled = true
  }

  # Server-side encryption with KMS.
  server_side_encryption {
    enabled     = true
    kms_key_arn = var.use_cmk ? var.kms_key_arn : null
  }

  tags = {
    Name    = "ecom_lakehouse_ingestion_ledger_${var.env}"
    Purpose = "idempotency-ledger"
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# WATERMARKS
# Purpose: tracks the last successfully processed batch per dataset.
# PK: dataset (string, e.g. "orders", "products", "order_items").
# Used to resume from the latest known-good state after failures.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_dynamodb_table" "watermarks" {
  name         = "ecom_lakehouse_watermarks_${var.env}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "dataset"

  attribute {
    name = "dataset"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = var.use_cmk ? var.kms_key_arn : null
  }

  tags = {
    Name    = "ecom_lakehouse_watermarks_${var.env}"
    Purpose = "batch-watermarks"
  }
}
