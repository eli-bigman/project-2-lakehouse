"""
config.py — Central configuration for ecom-lakehouse.

This module mirrors the Design Contract in docs/architecture.md §3 so that
every component imports a single authoritative source of truth instead of
hard-coding strings. Any change to naming or structure should be made here
first and will propagate to all consumers.

Two naming conventions exist intentionally:
  - Short dataset key:  "products" | "orders" | "order_items"
  - Canonical table:    "dim_products" | "fct_orders" | "fct_order_items"
All config dicts below key on the SHORT name.  DATASET_TO_TABLE maps short→table.
"""

import os

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
# Resolved from the TF_ENV environment variable; defaults to "dev" locally.
# The same suffix is appended to every stateful AWS resource.
ENV = os.environ.get("TF_ENV", "dev")

# ---------------------------------------------------------------------------
# Project prefix
# Matches architecture.md §3.1: "ecom-lakehouse" for resource names (kebab-case).
# ---------------------------------------------------------------------------
PROJECT_PREFIX = "ecom-lakehouse"

# ---------------------------------------------------------------------------
# AWS coordinates
# ---------------------------------------------------------------------------
AWS_ACCOUNT_ID = "647594457599"
AWS_REGION = "us-east-1"

# ---------------------------------------------------------------------------
# S3 Bucket Names  (architecture.md §3.2)
# Pattern: {PROJECT_PREFIX}-{zone}-{env}
# ---------------------------------------------------------------------------
# Raw landing — immutable original drops (versioned, Intelligent-Tiering optional)
RAW_BUCKET = f"{PROJECT_PREFIX}-raw-{ENV}"

# Staging / normalized — xlsx/csv→Parquet ephemeral (7-day lifecycle expiry)
STAGING_BUCKET = f"{PROJECT_PREFIX}-staging-{ENV}"

# Curated DWH — Delta Lake tables (indefinite retention, prevent_destroy)
DWH_BUCKET = f"{PROJECT_PREFIX}-dwh-{ENV}"

# Archive — post-ingest originals, s3://.../archive/{dataset}/{batch_id}/
ARCHIVE_BUCKET = f"{PROJECT_PREFIX}-archive-{ENV}"

# Quarantine — rejected records + reject reason (180-day lifecycle)
QUARANTINE_BUCKET = f"{PROJECT_PREFIX}-quarantine-{ENV}"

# Athena query results (workgroup-bound, 30-day lifecycle)
ATHENA_RESULTS_BUCKET = f"{PROJECT_PREFIX}-athena-results-{ENV}"

# Glue scripts, JARs, Step Functions ASL definitions (versioned)
ARTIFACTS_BUCKET = f"{PROJECT_PREFIX}-artifacts-{ENV}"

# ---------------------------------------------------------------------------
# Dataset-to-table mapping
# Short key → canonical Delta table name (architecture.md §3.3).
# ---------------------------------------------------------------------------
DATASET_TO_TABLE = {
    "products": "dim_products",
    "orders": "fct_orders",
    "order_items": "fct_order_items",
}

# ---------------------------------------------------------------------------
# Delta table paths on DWH bucket
# Pattern: s3://{dwh_bucket}/{table_name}/
# Keys are SHORT dataset names for consistency with all other dicts.
# ---------------------------------------------------------------------------
TABLE_PATH = {
    dataset: f"s3://{DWH_BUCKET}/{table}/"
    for dataset, table in DATASET_TO_TABLE.items()
}

# ---------------------------------------------------------------------------
# Staging S3 prefix
# Where the Lambda normalizer writes Parquet:
#   s3://{STAGING_BUCKET}/{dataset}/batch_id={batch_id}/part.parquet
# ---------------------------------------------------------------------------


def staging_prefix(dataset: str, batch_id: str) -> str:
    """Return the S3 prefix for a staging Parquet drop."""
    return f"s3://{STAGING_BUCKET}/{dataset}/batch_id={batch_id}/"


# ---------------------------------------------------------------------------
# Quarantine S3 prefix
# Where rejected rows are written after validation:
#   s3://{QUARANTINE_BUCKET}/{dataset}/batch_id={batch_id}/
# ---------------------------------------------------------------------------
def quarantine_prefix(dataset: str, batch_id: str) -> str:
    """Return the S3 prefix for quarantine rejected rows."""
    return f"s3://{QUARANTINE_BUCKET}/{dataset}/batch_id={batch_id}/"


# ---------------------------------------------------------------------------
# Raw key layout  (architecture.md §3.2)
# s3://{RAW_BUCKET}/{dataset}/{yyyy}/{mm}/{filename}
# e.g. .../orders/2025/04/orders_apr_2025.xlsx
# ---------------------------------------------------------------------------
def raw_key(dataset: str, year: str, month: str, filename: str) -> str:
    """Construct the canonical raw-zone S3 key for a source file."""
    return f"{dataset}/{year}/{month}/{filename}"


# ---------------------------------------------------------------------------
# Glue Data Catalog  (architecture.md §3.5)
# ---------------------------------------------------------------------------
# Database name — snake_case per conventions
GLUE_DATABASE = f"ecom_lakehouse_db_{ENV}"

# Athena workgroup — Athena results are routed here (enforced by workgroup policy)
ATHENA_WORKGROUP = f"ecom_lakehouse_wg_{ENV}"

# ---------------------------------------------------------------------------
# DynamoDB tables  (architecture.md §3.6)
# ---------------------------------------------------------------------------
# Ingestion ledger — idempotency: one row per file; tracks status, checksum, counts
DYNAMODB_LEDGER = f"ecom_lakehouse_ingestion_ledger_{ENV}"

# Watermarks — last successfully processed batch/period per dataset
DYNAMODB_WATERMARKS = f"ecom_lakehouse_watermarks_{ENV}"

# ---------------------------------------------------------------------------
# Merge keys  (architecture.md §3.4)
# Column used as the MATCH predicate in the Delta MERGE (upsert).
# Keys are SHORT dataset names.
# ---------------------------------------------------------------------------
MERGE_KEY = {
    "products": "product_id",
    "orders": "order_id",
    "order_items": "id",
}

# ---------------------------------------------------------------------------
# Z-ORDER columns per table  (architecture.md §3.4)
# Drives OPTIMIZE ZORDER BY — improves data skipping for the most-filtered cols.
#   dim_products  → department (filtered heavily in category analytics)
#   fct_orders    → order_date (primary range filter; promoted to partition at scale)
#   fct_order_items → order_date + product_id (join key + range filter combo)
# Keys are SHORT dataset names.
# ---------------------------------------------------------------------------
ZORDER_COLS = {
    "products": ["department"],
    "orders": ["order_date"],
    "order_items": ["order_date", "product_id"],
}

# ---------------------------------------------------------------------------
# Validation thresholds
# ---------------------------------------------------------------------------
# Maximum fraction of rows allowed to fail business-rule validation before the
# pipeline aborts and quarantines the entire batch.  5% means >5% rejected rows
# will raise a ValueError in logging_utils.gate().
REJECT_THRESHOLD = 0.05

# ---------------------------------------------------------------------------
# Processing order
# products must be loaded before order_items (FK referential integrity check
# on product_id against dim_products).
# ---------------------------------------------------------------------------
DATASET_LOAD_ORDER = ["products", "orders", "order_items"]

# ---------------------------------------------------------------------------
# Glue job configuration  (ADR-020)
# Fixed 2 workers of type G.1X; auto-scaling OFF.
# Never set NumberOfWorkers=1 — AWS API hard minimum is 2.
# Never use G.025X — streaming-only worker type.
# ---------------------------------------------------------------------------
GLUE_WORKER_TYPE = "G.1X"
GLUE_NUM_WORKERS = 2  # fixed; do not set auto-scaling
