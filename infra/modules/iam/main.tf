# FILE 7: infra/modules/iam/main.tf
# IAM roles and least-privilege inline policies for all pipeline components.
# Five roles:
#   1. normalize-lambda  — xlsx/csv → Parquet (raw read, staging write, ledger write)
#   2. glue-ingest       — Spark ETL (staging read, dwh/quarantine write, ledger update)
#   3. archive-lambda    — move originals from raw → archive after successful ingest
#   4. stepfunctions     — orchestrator (invoke lambdas, start Glue, Athena, SNS)
#   5. gha-deploy        — GitHub Actions OIDC role for Terraform deployments

# ─────────────────────────────────────────────────────────────────────────────
# DATA: GitHub OIDC provider (referenced, not created here — assume it exists
# or is created in a bootstrap step; we reference it by URL).
# ─────────────────────────────────────────────────────────────────────────────

data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

# ─────────────────────────────────────────────────────────────────────────────
# 1. NORMALIZE LAMBDA ROLE
# Used by: normalize Lambda (xlsx/csv → Parquet), validate_schema Lambda (reuses this role)
# Least-privilege: read raw, write staging, ledger DynamoDB ops.
# ─────────────────────────────────────────────────────────────────────────────

data "aws_iam_policy_document" "normalize_lambda_trust" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "normalize_lambda" {
  name               = "${var.prefix}-normalize-lambda-role-${var.env}"
  assume_role_policy = data.aws_iam_policy_document.normalize_lambda_trust.json

  tags = {
    Name    = "${var.prefix}-normalize-lambda-role-${var.env}"
    Purpose = "normalize-validate-lambda"
  }
}

data "aws_iam_policy_document" "normalize_lambda_policy" {
  # Read source files from raw landing zone.
  statement {
    sid       = "ReadRaw"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = ["${var.raw_bucket_arn}/*"]
  }

  # Write normalized Parquet to staging.
  statement {
    sid       = "WriteStaging"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${var.staging_bucket_arn}/*"]
  }

  # List staging for validation tasks.
  statement {
    sid       = "ListStagingAndRaw"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [var.raw_bucket_arn, var.staging_bucket_arn]
  }

  # Ledger: record file receipt and update status.
  statement {
    sid    = "LedgerWrite"
    effect = "Allow"
    actions = [
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:GetItem",
    ]
    resources = [
      var.ledger_table_arn,
      "${var.ledger_table_arn}/index/*",
    ]
  }
}

resource "aws_iam_role_policy" "normalize_lambda" {
  name   = "normalize-lambda-inline"
  role   = aws_iam_role.normalize_lambda.id
  policy = data.aws_iam_policy_document.normalize_lambda_policy.json
}

# AWS managed policy: basic Lambda execution (CloudWatch Logs).
resource "aws_iam_role_policy_attachment" "normalize_lambda_basic" {
  role       = aws_iam_role.normalize_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# ─────────────────────────────────────────────────────────────────────────────
# 2. GLUE INGEST ROLE
# Used by: all Glue Spark jobs (ingest, optimize).
# ADR-019: DynamicFrames prohibited; uses Spark DataFrames with Delta extensions.
# Explicit DENY on s3:DeleteObject for raw — Glue must never mutate source files.
# ─────────────────────────────────────────────────────────────────────────────

data "aws_iam_policy_document" "glue_ingest_trust" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["glue.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "glue_ingest" {
  name               = "${var.prefix}-glue-ingest-role-${var.env}"
  assume_role_policy = data.aws_iam_policy_document.glue_ingest_trust.json

  tags = {
    Name    = "${var.prefix}-glue-ingest-role-${var.env}"
    Purpose = "glue-spark-etl"
  }
}

data "aws_iam_policy_document" "glue_ingest_policy" {
  # Read normalized Parquet from staging.
  statement {
    sid       = "ReadStaging"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = ["${var.staging_bucket_arn}/*"]
  }

  # Read/write Delta tables in DWH.
  statement {
    sid       = "ReadWriteDwh"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:GetObjectVersion", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${var.dwh_bucket_arn}/*"]
  }

  # Write rejected records to quarantine.
  statement {
    sid       = "WriteQuarantine"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${var.quarantine_bucket_arn}/*"]
  }

  # Read Glue scripts and wheel packages from artifacts.
  statement {
    sid       = "ReadArtifacts"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = ["${var.artifacts_bucket_arn}/*"]
  }

  # List buckets Glue needs to traverse.
  statement {
    sid     = "ListBuckets"
    effect  = "Allow"
    actions = ["s3:ListBucket"]
    resources = [
      var.staging_bucket_arn,
      var.dwh_bucket_arn,
      var.quarantine_bucket_arn,
      var.artifacts_bucket_arn,
    ]
  }

  # Ledger + watermarks: update ingest state and advance watermark.
  statement {
    sid    = "DynamoDBControlPlane"
    effect = "Allow"
    actions = [
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:GetItem",
    ]
    resources = [
      var.ledger_table_arn,
      "${var.ledger_table_arn}/index/*",
      var.watermarks_table_arn,
    ]
  }

  # Publish custom Glue metrics to CloudWatch.
  statement {
    sid       = "CloudWatchMetrics"
    effect    = "Allow"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["EcomLakehouse/Glue"]
    }
  }

  # CloudWatch Logs for Glue continuous logging.
  statement {
    sid    = "CloudWatchLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["arn:aws:logs:us-east-1:${var.account_id}:log-group:/aws-glue/*"]
  }

  # EXPLICIT DENY: Glue must NEVER delete objects from the immutable raw zone.
  statement {
    sid       = "DenyRawDelete"
    effect    = "Deny"
    actions   = ["s3:DeleteObject", "s3:DeleteObjectVersion"]
    resources = ["${var.raw_bucket_arn}/*"]
  }
}

resource "aws_iam_role_policy" "glue_ingest" {
  name   = "glue-ingest-inline"
  role   = aws_iam_role.glue_ingest.id
  policy = data.aws_iam_policy_document.glue_ingest_policy.json
}

# AWS managed policy: standard Glue service permissions (Glue catalog, CloudWatch).
resource "aws_iam_role_policy_attachment" "glue_ingest_service" {
  role       = aws_iam_role.glue_ingest.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole"
}

# ─────────────────────────────────────────────────────────────────────────────
# 3. ARCHIVE LAMBDA ROLE
# Used by: archive Lambda (moves originals from raw → archive bucket post-ingest).
# ─────────────────────────────────────────────────────────────────────────────

data "aws_iam_policy_document" "archive_lambda_trust" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "archive_lambda" {
  name               = "${var.prefix}-archive-lambda-role-${var.env}"
  assume_role_policy = data.aws_iam_policy_document.archive_lambda_trust.json

  tags = {
    Name    = "${var.prefix}-archive-lambda-role-${var.env}"
    Purpose = "archive-lambda"
  }
}

data "aws_iam_policy_document" "archive_lambda_policy" {
  # Read the original file from raw to copy it to archive.
  statement {
    sid       = "ReadRaw"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = ["${var.raw_bucket_arn}/*"]
  }

  # Delete from raw ONLY after successful copy to archive (post-ingest cleanup).
  statement {
    sid       = "DeleteRaw"
    effect    = "Allow"
    actions   = ["s3:DeleteObject"]
    resources = ["${var.raw_bucket_arn}/*"]
  }

  # Write the copy to archive.
  statement {
    sid       = "WriteArchive"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${var.archive_bucket_arn}/*"]
  }

  # Update ledger status to ARCHIVED.
  statement {
    sid       = "LedgerUpdate"
    effect    = "Allow"
    actions   = ["dynamodb:UpdateItem"]
    resources = [var.ledger_table_arn]
  }
}

resource "aws_iam_role_policy" "archive_lambda" {
  name   = "archive-lambda-inline"
  role   = aws_iam_role.archive_lambda.id
  policy = data.aws_iam_policy_document.archive_lambda_policy.json
}

resource "aws_iam_role_policy_attachment" "archive_lambda_basic" {
  role       = aws_iam_role.archive_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# ─────────────────────────────────────────────────────────────────────────────
# 4. STEP FUNCTIONS ROLE
# Used by: the pipeline state machine.
# Needs to invoke all Lambda functions, start/poll Glue jobs, query Athena, alert SNS.
# ─────────────────────────────────────────────────────────────────────────────

data "aws_iam_policy_document" "stepfunctions_trust" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]

    # Scope trust to this account + region to prevent confused-deputy attacks.
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
  }
}

resource "aws_iam_role" "stepfunctions" {
  name               = "${var.prefix}-stepfunctions-role-${var.env}"
  assume_role_policy = data.aws_iam_policy_document.stepfunctions_trust.json

  tags = {
    Name    = "${var.prefix}-stepfunctions-role-${var.env}"
    Purpose = "pipeline-orchestrator"
  }
}

data "aws_iam_policy_document" "stepfunctions_policy" {
  # Invoke all pipeline Lambda functions.
  statement {
    sid     = "InvokeLambdas"
    effect  = "Allow"
    actions = ["lambda:InvokeFunction"]
    resources = [
      "arn:aws:lambda:us-east-1:${var.account_id}:function:${var.prefix}-normalize-${var.env}",
      "arn:aws:lambda:us-east-1:${var.account_id}:function:${var.prefix}-claim-file-${var.env}",
      "arn:aws:lambda:us-east-1:${var.account_id}:function:${var.prefix}-archive-file-${var.env}",
      "arn:aws:lambda:us-east-1:${var.account_id}:function:${var.prefix}-validate-schema-${var.env}",
    ]
  }

  # Start and monitor Glue jobs.
  statement {
    sid    = "GlueJobControl"
    effect = "Allow"
    actions = [
      "glue:StartJobRun",
      "glue:GetJobRun",
      "glue:GetJobRuns",
      "glue:BatchStopJobRun",
    ]
    resources = [
      "arn:aws:glue:us-east-1:${var.account_id}:job/${var.prefix}-ingest-${var.env}",
      "arn:aws:glue:us-east-1:${var.account_id}:job/${var.prefix}-optimize-${var.env}",
    ]
  }

  # Run Athena queries (post-ingest verification queries).
  statement {
    sid    = "AthenaQuery"
    effect = "Allow"
    actions = [
      "athena:StartQueryExecution",
      "athena:GetQueryExecution",
      "athena:GetQueryResults",
      "athena:StopQueryExecution",
    ]
    resources = [
      "arn:aws:athena:us-east-1:${var.account_id}:workgroup/ecom_lakehouse_wg_${var.env}",
    ]
  }

  # Athena needs S3 access for query results.
  statement {
    sid    = "AthenaResultsS3"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:GetBucketLocation",
      "s3:ListBucket",
    ]
    resources = [
      var.athena_results_bucket_arn,
      "${var.athena_results_bucket_arn}/*",
    ]
  }

  # Alert on pipeline failures.
  statement {
    sid       = "SNSAlerts"
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [var.sns_topic_arn]
  }

  # Allow the state machine to start a nested execution (child execution pattern).
  statement {
    sid     = "SelfExecute"
    effect  = "Allow"
    actions = ["states:StartExecution"]
    resources = [
      "arn:aws:states:us-east-1:${var.account_id}:stateMachine:${var.prefix}-sm-${var.env}",
    ]
  }

  # Deliver execution logs to CloudWatch.
  statement {
    sid    = "CloudWatchLogDelivery"
    effect = "Allow"
    actions = [
      "logs:CreateLogDelivery",
      "logs:GetLogDelivery",
      "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery",
      "logs:ListLogDeliveries",
      "logs:PutLogEvents",
      "logs:PutResourcePolicy",
      "logs:DescribeResourcePolicies",
      "logs:DescribeLogGroups",
    ]
    resources = ["*"]
  }

  # Allow XRay tracing (optional but recommended for SF debugging).
  statement {
    sid    = "XRayTracing"
    effect = "Allow"
    actions = [
      "xray:PutTraceSegments",
      "xray:PutTelemetryRecords",
      "xray:GetSamplingRules",
      "xray:GetSamplingTargets",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "stepfunctions" {
  name   = "stepfunctions-inline"
  role   = aws_iam_role.stepfunctions.id
  policy = data.aws_iam_policy_document.stepfunctions_policy.json
}

# ─────────────────────────────────────────────────────────────────────────────
# 5. GITHUB ACTIONS DEPLOY ROLE (OIDC)
# ADR-013: No long-lived credentials. GitHub Actions assumes this role via OIDC.
# Trust is scoped to the specific repo + main branch to prevent unauthorized access.
# AdministratorAccess is granted because Terraform must create all resource types.
# ─────────────────────────────────────────────────────────────────────────────

data "aws_iam_policy_document" "gha_deploy_trust" {
  statement {
    effect = "Allow"

    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github.arn]
    }

    actions = ["sts:AssumeRoleWithWebIdentity"]

    # Restrict to the specific repo and main branch.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_org}/${var.github_repo}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "gha_deploy" {
  name                 = "${var.prefix}-gha-deploy-role-${var.env}"
  assume_role_policy   = data.aws_iam_policy_document.gha_deploy_trust.json
  max_session_duration = 3600

  tags = {
    Name    = "${var.prefix}-gha-deploy-role-${var.env}"
    Purpose = "github-actions-terraform-deploy"
  }
}

# Terraform needs broad access to create/update/delete all resource types.
# In prod, consider replacing with a tightly scoped policy post-stabilisation.
resource "aws_iam_role_policy_attachment" "gha_deploy_admin" {
  role       = aws_iam_role.gha_deploy.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

# ─────────────────────────────────────────────────────────────────────────────
# EventBridge → Step Functions invocation role
# Required so EventBridge can start the state machine when S3 events arrive.
# Created here rather than in the stepfunctions module to avoid circular refs.
# ─────────────────────────────────────────────────────────────────────────────

data "aws_iam_policy_document" "eventbridge_sf_trust" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
  }
}

resource "aws_iam_role" "eventbridge_sf" {
  name               = "${var.prefix}-eventbridge-sf-role-${var.env}"
  assume_role_policy = data.aws_iam_policy_document.eventbridge_sf_trust.json

  tags = {
    Name    = "${var.prefix}-eventbridge-sf-role-${var.env}"
    Purpose = "eventbridge-start-stepfunctions"
  }
}

data "aws_iam_policy_document" "eventbridge_sf_policy" {
  statement {
    sid     = "StartStateMachine"
    effect  = "Allow"
    actions = ["states:StartExecution"]
    resources = [
      "arn:aws:states:us-east-1:${var.account_id}:stateMachine:${var.prefix}-sm-${var.env}",
    ]
  }
}

resource "aws_iam_role_policy" "eventbridge_sf" {
  name   = "eventbridge-sf-inline"
  role   = aws_iam_role.eventbridge_sf.id
  policy = data.aws_iam_policy_document.eventbridge_sf_policy.json
}
