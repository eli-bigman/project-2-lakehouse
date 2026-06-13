"""
validate_schema.py — Lambda: structural schema gate before Glue fan-out.

This Lambda runs after normalize_to_parquet and before the Glue Spark jobs.
Its purpose is to validate that the staging Parquet has the expected column
names so that Glue jobs fail fast at the source rather than deep in a Spark
plan where error messages are harder to read.

What it checks:
  - The Parquet file in staging has all columns listed in EXPECTED_COLUMNS
    for the given dataset.
  - Any extra columns (not in EXPECTED_COLUMNS) are reported but do NOT
    cause failure — they will be dropped by the Glue job.
  - Audit columns (_ingest_ts etc.) are NOT expected here because they are
    added by transforms.derive() in the Glue job, not by the Lambda normalizer.

Implementation uses PyArrow's schema inspection (read_schema) rather than
reading the full Parquet into memory — this avoids loading potentially large
files into Lambda just to check column names.

Step Functions wires this Lambda before the Glue fan-out.  If schema_valid
is False, the workflow transitions to a FAIL state with the missing_cols
information attached, triggering an SNS alert.
"""

import logging
import os
import tempfile
from typing import Any, Dict, List

import boto3
import pyarrow.parquet as pq

from lakehouse.schemas import EXPECTED_COLUMNS

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

s3_client = boto3.client("s3")


def _parse_s3_uri(uri: str):
    """Split 's3://bucket/key' into (bucket, key)."""
    assert uri.startswith("s3://"), f"Not an S3 URI: {uri}"
    parts = uri[len("s3://"):].split("/", 1)
    if len(parts) != 2:
        raise ValueError(f"Cannot parse S3 URI: {uri}")
    return parts[0], parts[1]


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda entry point: validate staging Parquet column structure.

    Event shape:
        {
            "staging_uri": "s3://ecom-lakehouse-staging-dev/orders/batch_id=.../part.parquet",
            "dataset":     "orders",
            "batch_id":    "orders-20250401-abc123"
        }

    Returns:
        {
            "schema_valid":  True | False,
            "missing_cols":  [],          # columns in EXPECTED_COLUMNS not in file
            "extra_cols":    [],          # columns in file not in EXPECTED_COLUMNS
            "actual_cols":   [...],       # all columns found in the Parquet file
            "dataset":       "orders",
            "batch_id":      "orders-20250401-abc123"
        }
    """
    # ------------------------------------------------------------------
    # Validate event
    # ------------------------------------------------------------------
    required_keys = ["staging_uri", "dataset", "batch_id"]
    missing_event = [k for k in required_keys if k not in event]
    if missing_event:
        raise ValueError(f"validate_schema: missing event keys: {missing_event}")

    staging_uri: str = event["staging_uri"]
    dataset: str = event["dataset"]
    batch_id: str = event["batch_id"]

    logger.info(
        "Schema validation: dataset=%s batch_id=%s staging_uri=%s",
        dataset, batch_id, staging_uri,
    )

    if dataset not in EXPECTED_COLUMNS:
        raise ValueError(
            f"validate_schema: unknown dataset '{dataset}'. "
            f"Supported: {list(EXPECTED_COLUMNS.keys())}"
        )

    expected_cols: List[str] = EXPECTED_COLUMNS[dataset]

    # ------------------------------------------------------------------
    # Read the Parquet schema only (no data rows loaded into memory)
    # Download a copy to /tmp/ so pyarrow can read it locally.
    # ------------------------------------------------------------------
    bucket, key = _parse_s3_uri(staging_uri)
    local_path = os.path.join(tempfile.gettempdir(), f"{batch_id}_schema_check.parquet")

    try:
        s3_client.download_file(bucket, key, local_path)
    except Exception as exc:
        raise RuntimeError(
            f"validate_schema: failed to download {staging_uri}: {exc}"
        ) from exc

    # Use pyarrow.parquet.read_schema() — reads footer metadata only, not row groups.
    # This is O(1) in file size and very fast.
    try:
        parquet_schema = pq.read_schema(local_path)
        actual_cols: List[str] = parquet_schema.names
    except Exception as exc:
        raise RuntimeError(
            f"validate_schema: failed to read Parquet schema from {local_path}: {exc}"
        ) from exc
    finally:
        # Clean up /tmp/ — Lambda reuses execution environments, so tmp can fill up
        if os.path.exists(local_path):
            os.remove(local_path)

    # ------------------------------------------------------------------
    # Compare actual columns against expected columns
    # ------------------------------------------------------------------
    missing_cols: List[str] = [c for c in expected_cols if c not in actual_cols]
    extra_cols: List[str] = [c for c in actual_cols if c not in expected_cols]
    schema_valid: bool = len(missing_cols) == 0

    if not schema_valid:
        logger.error(
            "Schema validation FAILED: dataset=%s missing_cols=%s",
            dataset, missing_cols,
        )
    elif extra_cols:
        logger.warning(
            "Schema validation passed with extra columns: dataset=%s extra_cols=%s "
            "(they will be dropped by the Glue job)",
            dataset, extra_cols,
        )
    else:
        logger.info("Schema validation PASSED: dataset=%s all columns present", dataset)

    return {
        "schema_valid": schema_valid,
        "missing_cols": missing_cols,
        "extra_cols": extra_cols,
        "actual_cols": actual_cols,
        "dataset": dataset,
        "batch_id": batch_id,
    }
