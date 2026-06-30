# FILE 15: infra/modules/lambda/main.tf
# Four Lambda functions in the pipeline:
#   1. normalize      — xlsx/csv → Parquet (300s, 1024 MB)
#   2. claim_file     — idempotency check + ledger write before processing (60s, 256 MB)
#   3. archive_file   — move original from raw → archive post-ingest (60s, 256 MB)
#   4. validate_schema — structural validation before Glue (60s, 256 MB)
#
# Deployment model: Lambda packages are built by CI/CD and uploaded to S3.
#   The placeholder ZIP contains a minimal handler to allow `terraform apply`
#   on first boot. CI/CD updates the function code via `aws lambda update-function-code`.
#   source_code_hash is computed from the S3 object — Terraform triggers code updates.
#
# Step Functions owns all retries — max_retry_attempts=0 on all functions.

# ── DATA SOURCES FOR LAMBDA LAYERS ───────────────────────────────────────────
data "aws_ssm_parameter" "pandas_layer" {
  name = "/aws/service/aws-sdk-pandas/3.11.0/py3.11/x86_64/layer-arn"
}

data "aws_lambda_layer_version" "openpyxl" {
  layer_name = "${var.prefix}-openpyxl-${var.env}"
}

locals {
  # Shared environment variables injected into all Lambda functions.
  common_env = {
    LEDGER_TABLE     = var.ledger_table_name
    WATERMARKS_TABLE = var.watermarks_table_name
    ENV              = var.env
    RAW_BUCKET       = var.raw_bucket
    STAGING_BUCKET   = var.staging_bucket
    DWH_BUCKET       = var.dwh_bucket
    ARCHIVE_BUCKET   = var.archive_bucket
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# PLACEHOLDER ZIP
# First-apply bootstrap: a minimal Python stub so the function exists in AWS.
# CI/CD overwrites this with the real package via S3.
# ─────────────────────────────────────────────────────────────────────────────

data "archive_file" "placeholder" {
  type        = "zip"
  output_path = "${path.module}/placeholder.zip"

  source {
    content  = "def handler(event, context): return {'statusCode': 200, 'body': 'placeholder'}"
    filename = "handler.py"
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# 1. NORMALIZE LAMBDA
# Converts raw xlsx/csv files to Parquet in staging.
# High memory (1024 MB) for pandas + openpyxl workload (ADR-002).
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_lambda_function" "normalize" {
  function_name = "${var.prefix}-normalize-${var.env}"
  description   = "Normalize raw xlsx/csv to Parquet in staging (ADR-002)."
  role          = var.normalize_role_arn
  runtime       = "python3.11"
  handler       = "normalize.handler"

  # Placeholder ZIP on first apply; CI/CD updates via aws lambda update-function-code.
  filename         = data.archive_file.placeholder.output_path
  source_code_hash = data.archive_file.placeholder.output_base64sha256

  # 300s: xlsx files with thousands of rows can be slow on pandas.
  timeout     = 300
  memory_size = 1024

  layers = [
    data.aws_ssm_parameter.pandas_layer.value,
    data.aws_lambda_layer_version.openpyxl.arn
  ]

  environment {
    variables = local.common_env
  }

  tags = {
    Name    = "${var.prefix}-normalize-${var.env}"
    Purpose = "xlsx-csv-to-parquet"
  }
}

resource "aws_lambda_function_event_invoke_config" "normalize" {
  function_name                = aws_lambda_function.normalize.function_name
  maximum_event_age_in_seconds = 300
  # Step Functions owns retries — set to 0 here.
  maximum_retry_attempts = 0
}

# ─────────────────────────────────────────────────────────────────────────────
# 2. CLAIM FILE LAMBDA
# Idempotency gate: writes CLAIMED status to ledger before processing starts.
# Fast function — DynamoDB conditional write only.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_lambda_function" "claim_file" {
  function_name = "${var.prefix}-claim-file-${var.env}"
  description   = "Write CLAIMED status to ledger; reject duplicate file_key."
  role          = var.claim_role_arn
  runtime       = "python3.11"
  handler       = "claim_file.handler"

  filename         = data.archive_file.placeholder.output_path
  source_code_hash = data.archive_file.placeholder.output_base64sha256

  timeout     = 60
  memory_size = 256

  environment {
    variables = local.common_env
  }

  tags = {
    Name    = "${var.prefix}-claim-file-${var.env}"
    Purpose = "idempotency-gate"
  }
}

resource "aws_lambda_function_event_invoke_config" "claim_file" {
  function_name                = aws_lambda_function.claim_file.function_name
  maximum_event_age_in_seconds = 300
  maximum_retry_attempts       = 0
}

# ─────────────────────────────────────────────────────────────────────────────
# 3. ARCHIVE FILE LAMBDA
# Moves original from raw → archive bucket after successful ingest.
# Updates ledger status to ARCHIVED.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_lambda_function" "archive_file" {
  function_name = "${var.prefix}-archive-file-${var.env}"
  description   = "Copy original from raw → archive, then delete from raw."
  role          = var.archive_role_arn
  runtime       = "python3.11"
  handler       = "archive_file.handler"

  filename         = data.archive_file.placeholder.output_path
  source_code_hash = data.archive_file.placeholder.output_base64sha256

  timeout     = 60
  memory_size = 256

  environment {
    variables = local.common_env
  }

  tags = {
    Name    = "${var.prefix}-archive-file-${var.env}"
    Purpose = "post-ingest-archival"
  }
}

resource "aws_lambda_function_event_invoke_config" "archive_file" {
  function_name                = aws_lambda_function.archive_file.function_name
  maximum_event_age_in_seconds = 300
  maximum_retry_attempts       = 0
}

# ─────────────────────────────────────────────────────────────────────────────
# 4. VALIDATE SCHEMA LAMBDA
# Structural validation of normalized Parquet before Glue ingest.
# Checks column presence, dtypes, null constraints against Design Contract §3.3.
# Reuses normalize role ARN (same S3 read perms on staging, ledger update).
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_lambda_function" "validate_schema" {
  function_name = "${var.prefix}-validate-schema-${var.env}"
  description   = "Validate Parquet schema + constraints before Glue job invocation."
  role          = var.validate_schema_role_arn
  runtime       = "python3.11"
  handler       = "validate_schema.handler"

  filename         = data.archive_file.placeholder.output_path
  source_code_hash = data.archive_file.placeholder.output_base64sha256

  timeout     = 60
  memory_size = 256

  layers = [
    data.aws_ssm_parameter.pandas_layer.value
  ]

  environment {
    variables = local.common_env
  }

  tags = {
    Name    = "${var.prefix}-validate-schema-${var.env}"
    Purpose = "pre-glue-schema-validation"
  }
}

resource "aws_lambda_function_event_invoke_config" "validate_schema" {
  function_name                = aws_lambda_function.validate_schema.function_name
  maximum_event_age_in_seconds = 300
  maximum_retry_attempts       = 0
}
