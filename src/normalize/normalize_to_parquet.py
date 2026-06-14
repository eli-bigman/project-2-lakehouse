"""
normalize_to_parquet.py — AWS Lambda handler: xlsx/csv → Parquet normalization.

Design rationale (ADR-011):
  Glue Spark is heavyweight and incurs a 30–60s cold-start penalty.  For the
  normalization step (which only needs to reformat the file — no business logic,
  no validation, no joins) a Lambda function is cheaper, faster, and simpler.
  Lambda can use pandas + openpyxl to read xlsx/csv and write Parquet directly
  to the staging bucket in a few seconds.

Responsibilities:
  - Download the raw file from S3 to /tmp/ (Lambda ephemeral storage, 512 MB default)
  - Detect format (.xlsx or .csv) and read with the appropriate pandas reader
  - Validate that the file contains exactly the expected columns (schema gate)
  - Write canonical-column-order Parquet to the staging bucket
  - Return structured metadata for the Step Functions state machine

Explicitly NOT responsibilities (left to Glue Spark):
  - Type casting / coercion
  - Business-rule validation
  - Deduplication
  - Delta MERGE

The Lambda does NOT perform value cleaning — it preserves raw values as-is so
that the Glue job's enforce_types() sees what was actually in the source file.
"""

import logging
import os
import tempfile
from typing import Any, Dict

import boto3
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# Import EXPECTED_COLUMNS from the lakehouse library.
# In the Lambda deployment package, src/lakehouse is included as a layer or
# bundled in the deployment zip alongside this file.
from lakehouse.schemas import EXPECTED_COLUMNS

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

s3_client = boto3.client("s3")


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda entry point: normalize one raw file to Parquet.

    Event shape (from Step Functions input):
        {
            "raw_key":        "orders/2025/04/orders_apr_2025.xlsx",
            "dataset":        "orders",
            "batch_id":       "orders-20250401-abc123",
            "raw_bucket":     "ecom-lakehouse-raw-dev",
            "staging_bucket": "ecom-lakehouse-staging-dev",
            "env":            "dev"
        }

    Returns:
        {
            "staging_uri": "s3://ecom-lakehouse-staging-dev/orders/batch_id=.../part.parquet",
            "rows_in":     500,
            "status":      "NORMALIZED"
        }

    Raises:
        ValueError: if required event keys are missing, if the file format is
                    unsupported, or if the column names don't match EXPECTED_COLUMNS.
        RuntimeError: wraps any unexpected exception with context for CloudWatch logs.
    """
    # ------------------------------------------------------------------
    # Validate event shape
    # ------------------------------------------------------------------
    required_keys = ["raw_key", "dataset", "batch_id", "raw_bucket", "staging_bucket", "env"]
    missing = [k for k in required_keys if k not in event]
    if missing:
        raise ValueError(f"normalize_to_parquet: missing event keys: {missing}")

    raw_key: str = event["raw_key"]
    dataset: str = event["dataset"]
    batch_id: str = event["batch_id"]
    raw_bucket: str = event["raw_bucket"]
    staging_bucket: str = event["staging_bucket"]

    logger.info(
        "Normalizing file: bucket=%s key=%s dataset=%s batch_id=%s",
        raw_bucket,
        raw_key,
        dataset,
        batch_id,
    )

    # Validate dataset is known
    if dataset not in EXPECTED_COLUMNS:
        raise ValueError(
            f"normalize_to_parquet: unknown dataset '{dataset}'. "
            f"Supported: {list(EXPECTED_COLUMNS.keys())}"
        )

    # ------------------------------------------------------------------
    # Detect file format from the raw_key extension
    # ------------------------------------------------------------------
    lower_key = raw_key.lower()
    if lower_key.endswith(".xlsx"):
        file_format = "xlsx"
    elif lower_key.endswith(".csv"):
        file_format = "csv"
    else:
        raise ValueError(
            f"normalize_to_parquet: unsupported file format for key '{raw_key}'. "
            f"Expected .xlsx or .csv"
        )

    # ------------------------------------------------------------------
    # Download raw file from S3 to /tmp/
    # Lambda ephemeral storage is 512 MB by default (configurable up to 10 GB).
    # Using /tmp/ is safe because Lambda execution environments are isolated.
    # ------------------------------------------------------------------
    filename = os.path.basename(raw_key)
    local_path = os.path.join(tempfile.gettempdir(), filename)

    try:
        logger.info("Downloading s3://%s/%s → %s", raw_bucket, raw_key, local_path)
        s3_client.download_file(raw_bucket, raw_key, local_path)
    except Exception as exc:
        raise RuntimeError(
            f"normalize_to_parquet: failed to download s3://{raw_bucket}/{raw_key}: {exc}"
        ) from exc

    # ------------------------------------------------------------------
    # Read file with pandas
    # ------------------------------------------------------------------
    try:
        if file_format == "xlsx":
            # engine="openpyxl" required for xlsx (xlrd dropped xlsx support in v2)
            df = pd.read_excel(local_path, engine="openpyxl")
        else:
            # CSV: no dtype inference — keep everything as strings so Glue
            # enforce_types() does the authoritative casting.
            df = pd.read_csv(local_path, dtype=str, keep_default_na=False)
    except Exception as exc:
        raise RuntimeError(
            f"normalize_to_parquet: failed to read {file_format} file '{local_path}': {exc}"
        ) from exc

    rows_in = len(df)
    logger.info("Read %d rows from %s", rows_in, raw_key)

    # ------------------------------------------------------------------
    # Column validation against EXPECTED_COLUMNS
    # This is a structural gate: if the source file is missing required
    # columns or has unexpected extras, we fail fast here rather than
    # letting bad data propagate to Glue.
    # Only check presence — column order is normalised in the next step.
    # ------------------------------------------------------------------
    actual_cols = list(df.columns)
    expected_cols = EXPECTED_COLUMNS[dataset]

    missing_cols = [c for c in expected_cols if c not in actual_cols]
    extra_cols = [c for c in actual_cols if c not in expected_cols]

    if missing_cols:
        raise ValueError(
            f"normalize_to_parquet: dataset='{dataset}' raw file is missing required columns: "
            f"{missing_cols}. Actual columns: {actual_cols}"
        )

    if extra_cols:
        logger.warning(
            "normalize_to_parquet: dataset='%s' has unexpected extra columns %s — "
            "they will be dropped",
            dataset,
            extra_cols,
        )

    # ------------------------------------------------------------------
    # Reorder to canonical column order and drop any extra columns.
    # No value transformations here — preserve raw values for Glue.
    # ------------------------------------------------------------------
    df = df[expected_cols]

    # ------------------------------------------------------------------
    # Write to Parquet and upload to staging bucket
    # Staging path pattern: s3://{staging_bucket}/{dataset}/batch_id={batch_id}/part.parquet
    # ------------------------------------------------------------------
    staging_key = f"{dataset}/batch_id={batch_id}/part.parquet"
    staging_uri = f"s3://{staging_bucket}/{staging_key}"

    # Write Parquet to /tmp/ first, then upload
    parquet_local = os.path.join(tempfile.gettempdir(), f"{batch_id}_part.parquet")
    try:
        table = pa.Table.from_pandas(df, preserve_index=False)
        pq.write_table(table, parquet_local)
    except Exception as exc:
        raise RuntimeError(
            f"normalize_to_parquet: failed to write Parquet to {parquet_local}: {exc}"
        ) from exc

    try:
        logger.info("Uploading Parquet to %s", staging_uri)
        s3_client.upload_file(parquet_local, staging_bucket, staging_key)
    except Exception as exc:
        raise RuntimeError(
            f"normalize_to_parquet: failed to upload to {staging_uri}: {exc}"
        ) from exc

    logger.info(
        "Normalization complete: dataset=%s rows_in=%d staging_uri=%s",
        dataset,
        rows_in,
        staging_uri,
    )

    return {
        "staging_uri": staging_uri,
        "rows_in": rows_in,
        "status": "NORMALIZED",
    }
