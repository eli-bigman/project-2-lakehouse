# FILE 17: infra/envs/dev/main.tf
# Dev environment — composes all infrastructure modules.
#
# IMPORTANT: Module dependency order is managed implicitly by Terraform's
# dependency graph. To avoid cycles:
#   - observability receives glue_job_names and state_machine_name as CONSTRUCTED
#     LOCALS (not module outputs). This breaks the would-be cycle:
#       observability → (needs) → glue → (needs) → iam → (needs) → observability.sns_topic_arn
#   - The iam module receives sns_topic_arn from observability (one-way dependency).
#
# dev-specific settings:
#   use_cmk = false  → AWS-managed keys (no CMK cost in dev, ADR-017)
#   protect_stateful → passed from var.protect_stateful (default true)

locals {
  env    = "dev"
  prefix = "ecom-lakehouse"

  account_id = var.account_id

  # Constructed resource names — used to break module dependency cycles.
  # These match the naming convention used inside each module exactly.
  glue_ingest_job_name   = "${local.prefix}-ingest-${local.env}"
  glue_optimize_job_name = "${local.prefix}-optimize-${local.env}"
  state_machine_name     = "${local.prefix}-sm-${local.env}"
}

# ─────────────────────────────────────────────────────────────────────────────
# S3 ZONES
# ─────────────────────────────────────────────────────────────────────────────

module "s3_zones" {
  source = "../../modules/s3_zones"

  env              = local.env
  prefix           = local.prefix
  use_cmk          = false # dev: AWS-managed key (ADR-017)
  kms_key_arn      = ""    # not used in dev
  protect_stateful = var.protect_stateful
}

# ─────────────────────────────────────────────────────────────────────────────
# DYNAMODB CONTROL PLANE
# ─────────────────────────────────────────────────────────────────────────────

module "dynamodb" {
  source = "../../modules/dynamodb"

  env         = local.env
  prefix      = local.prefix
  use_cmk     = false # dev: AWS-managed DynamoDB key (ADR-017)
  kms_key_arn = ""
}

# ─────────────────────────────────────────────────────────────────────────────
# OBSERVABILITY (SNS, CloudWatch)
# Must be created before IAM (IAM needs sns_topic_arn).
# glue_job_names and state_machine_name are locals (not module outputs) to prevent cycles.
# ─────────────────────────────────────────────────────────────────────────────

module "observability" {
  source = "../../modules/observability"

  env    = local.env
  prefix = local.prefix

  alert_email = var.alert_email
  account_id  = local.account_id

  # Use constructed locals — do NOT reference module.glue.* here (cycle risk).
  glue_job_names     = [local.glue_ingest_job_name, local.glue_optimize_job_name]
  state_machine_name = local.state_machine_name

  # dev: alias/aws/sns (handled inside module when kms_key_arn is empty).
  kms_key_arn = ""
}

# ─────────────────────────────────────────────────────────────────────────────
# IAM ROLES AND POLICIES
# Depends on: s3_zones, dynamodb, observability (for sns_topic_arn).
# ─────────────────────────────────────────────────────────────────────────────

module "iam" {
  source = "../../modules/iam"

  env        = local.env
  prefix     = local.prefix
  account_id = local.account_id

  # S3 bucket ARNs from s3_zones outputs.
  raw_bucket_arn            = module.s3_zones.raw_bucket_arn
  staging_bucket_arn        = module.s3_zones.staging_bucket_arn
  dwh_bucket_arn            = module.s3_zones.dwh_bucket_arn
  archive_bucket_arn        = module.s3_zones.archive_bucket_arn
  quarantine_bucket_arn     = module.s3_zones.quarantine_bucket_arn
  artifacts_bucket_arn      = module.s3_zones.artifacts_bucket_arn
  athena_results_bucket_arn = module.s3_zones.athena_results_bucket_arn

  # DynamoDB ARNs from dynamodb outputs.
  ledger_table_arn     = module.dynamodb.ledger_table_arn
  watermarks_table_arn = module.dynamodb.watermarks_table_arn

  # SNS topic ARN from observability outputs.
  sns_topic_arn = module.observability.sns_topic_arn

  # GitHub OIDC trust.
  github_org  = var.github_org
  github_repo = var.github_repo
}

# ─────────────────────────────────────────────────────────────────────────────
# GLUE CATALOG + JOBS
# Depends on: s3_zones (bucket names), iam (role ARN).
# ─────────────────────────────────────────────────────────────────────────────

module "glue" {
  source = "../../modules/glue"

  env    = local.env
  prefix = local.prefix

  dwh_bucket_name       = module.s3_zones.dwh_bucket_name
  artifacts_bucket_name = module.s3_zones.artifacts_bucket_name
  glue_role_arn         = module.iam.glue_ingest_role_arn

  # Use default db naming convention (empty → "ecom_lakehouse_db_dev").
  database_name = ""
}

# ─────────────────────────────────────────────────────────────────────────────
# LAMBDA FUNCTIONS
# Depends on: iam (role ARNs), s3_zones (bucket names), dynamodb (table names).
# ─────────────────────────────────────────────────────────────────────────────

module "lambda" {
  source = "../../modules/lambda"

  env    = local.env
  prefix = local.prefix

  # IAM role ARNs.
  normalize_role_arn = module.iam.normalize_lambda_role_arn
  # claim_file reuses normalize role (same DynamoDB ledger + S3 staging perms).
  claim_role_arn   = module.iam.normalize_lambda_role_arn
  archive_role_arn = module.iam.archive_lambda_role_arn
  # validate_schema reuses normalize role (reads staging, updates ledger).
  validate_schema_role_arn = module.iam.normalize_lambda_role_arn

  # DynamoDB table names.
  ledger_table_name     = module.dynamodb.ledger_table_name
  watermarks_table_name = module.dynamodb.watermarks_table_name

  # S3 bucket names.
  raw_bucket       = module.s3_zones.raw_bucket_name
  staging_bucket   = module.s3_zones.staging_bucket_name
  dwh_bucket       = module.s3_zones.dwh_bucket_name
  archive_bucket   = module.s3_zones.archive_bucket_name
  artifacts_bucket = module.s3_zones.artifacts_bucket_name
}

# ─────────────────────────────────────────────────────────────────────────────
# STEP FUNCTIONS + EVENTBRIDGE TRIGGER
# Depends on: lambda (function ARNs), glue (job names), observability (SNS ARN),
#             iam (SF role + EventBridge role ARNs), s3_zones (raw bucket).
# ─────────────────────────────────────────────────────────────────────────────

module "stepfunctions" {
  source = "../../modules/stepfunctions"

  env    = local.env
  prefix = local.prefix

  # IAM roles.
  sf_role_arn          = module.iam.stepfunctions_role_arn
  eventbridge_role_arn = module.iam.eventbridge_sf_role_arn

  # Lambda function ARNs.
  normalize_fn_arn       = module.lambda.normalize_function_arn
  claim_fn_arn           = module.lambda.claim_file_function_arn
  archive_fn_arn         = module.lambda.archive_file_function_arn
  validate_schema_fn_arn = module.lambda.validate_schema_function_arn

  # Glue job names — use module outputs (glue module already created, no cycle).
  glue_ingest_job_name   = module.glue.ingest_job_name
  glue_optimize_job_name = module.glue.optimize_job_name

  # SNS topic for failure notifications.
  sns_topic_arn = module.observability.sns_topic_arn

  # Raw bucket trigger.
  raw_bucket_name = module.s3_zones.raw_bucket_name
  raw_bucket_arn  = module.s3_zones.raw_bucket_arn
}

# ─────────────────────────────────────────────────────────────────────────────
# OUTPUTS (convenience — exposed at the env level for CI/CD and operators)
# ─────────────────────────────────────────────────────────────────────────────

output "state_machine_arn" {
  description = "ARN of the Step Functions ingestion pipeline state machine."
  value       = module.stepfunctions.state_machine_arn
}

output "raw_bucket_name" {
  description = "Name of the raw landing S3 bucket."
  value       = module.s3_zones.raw_bucket_name
}

output "dwh_bucket_name" {
  description = "Name of the curated DWH S3 bucket."
  value       = module.s3_zones.dwh_bucket_name
}

output "glue_ingest_job_name" {
  description = "Name of the Glue ingest job."
  value       = module.glue.ingest_job_name
}

output "gha_deploy_role_arn" {
  description = "ARN for GitHub Actions OIDC role — add to GitHub repo secrets as AWS_ROLE_ARN."
  value       = module.iam.gha_deploy_role_arn
}

output "sns_topic_arn" {
  description = "ARN of the SNS alerts topic."
  value       = module.observability.sns_topic_arn
}
