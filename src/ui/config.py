"""
src/ui/config.py — Centralised environment-variable configuration for the Streamlit UI.

All configuration is read from environment variables so the same image can be
pointed at dev or prod by setting variables, with no code changes.
"""

import os


def _bool(value: str, default: bool = False) -> bool:
    """Parse a boolean environment variable (true/1/yes → True)."""
    return value.strip().lower() in ("true", "1", "yes") if value else default


def _int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# AWS / infrastructure
# ---------------------------------------------------------------------------

AWS_PROFILE: str | None = os.environ.get("AWS_PROFILE")  # e.g. "sandbox-lakehouse-dev"; None in CI/OIDC
AWS_REGION: str = os.environ.get("AWS_REGION", "eu-west-1")

S3_RAW_BUCKET: str = os.environ.get("S3_RAW_BUCKET", "ecom-lakehouse-raw-dev")
S3_ARTIFACTS_BUCKET: str = os.environ.get("S3_ARTIFACTS_BUCKET", "ecom-lakehouse-artifacts-dev")

# ---------------------------------------------------------------------------
# Athena
# ---------------------------------------------------------------------------

ATHENA_WORKGROUP: str = os.environ.get("ATHENA_WORKGROUP", "primary")
ATHENA_DATABASE: str = os.environ.get("ATHENA_DATABASE", "ecom_lakehouse_db_dev")
ATHENA_RESULTS_URI: str = os.environ.get(
    "ATHENA_RESULTS_URI", "s3://ecom-lakehouse-athena-results-dev/results/"
)

# ---------------------------------------------------------------------------
# DynamoDB tables
# ---------------------------------------------------------------------------

DYNAMODB_LEDGER_TABLE: str = os.environ.get(
    "DYNAMODB_LEDGER_TABLE", "ecom_lakehouse_ingestion_ledger_dev"
)
DYNAMODB_WATERMARKS_TABLE: str = os.environ.get(
    "DYNAMODB_WATERMARKS_TABLE", "ecom_lakehouse_watermarks_dev"
)

# ---------------------------------------------------------------------------
# Step Functions
# ---------------------------------------------------------------------------

STEP_FUNCTIONS_ARN: str = os.environ.get(
    "STEP_FUNCTIONS_ARN",
    "arn:aws:states:eu-west-1:970547336735:stateMachine:ecom-lakehouse-sm-dev",
)

# ---------------------------------------------------------------------------
# UI behaviour
# ---------------------------------------------------------------------------

# Label shown in the sidebar environment badge
UI_ENV_LABEL: str = os.environ.get("UI_ENV_LABEL", "dev")

# Hard cap on rows returned by Athena queries to prevent large result sets
UI_ATHENA_MAX_ROWS: int = _int(os.environ.get("UI_ATHENA_MAX_ROWS", ""), 1000)

# Gate the manual batch trigger page (set to "true" only in dev)
UI_ENABLE_TRIGGER: bool = _bool(os.environ.get("UI_ENABLE_TRIGGER", "false"))
