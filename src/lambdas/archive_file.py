"""
archive_file.py — Lambda: archive a raw file after a successful Glue load.

This Lambda is invoked by Step Functions after the Glue ingest job
successfully completes.  It:
  1. Copies the raw file from the raw zone to the archive zone.
  2. Deletes the source key from the raw zone (copy-then-delete pattern).
  3. Updates the DynamoDB ledger to status=ARCHIVED with the archive URI.

Why copy+delete instead of S3 move?
  S3 has no atomic move/rename operation.  The copy+delete pattern ensures:
    - The archive is written before the source is removed.
    - If the copy fails, the source is untouched (safe to retry).
    - If the delete fails, there is a duplicate in raw+archive — acceptable;
      the ledger status prevents re-processing.

Archive path pattern:
  s3://{archive_bucket}/{dataset}/{batch_id}/{filename}
  e.g. s3://ecom-lakehouse-archive-dev/orders/orders-20250401-abc123/orders_apr_2025.xlsx
"""

import logging
import os
from typing import Any, Dict

import boto3

from lakehouse.ledger import LedgerClient

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

s3_client = boto3.client("s3")


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda entry point: archive raw file after successful load.

    Event shape:
        {
            "file_key":      "orders/2025/04/orders_apr_2025.xlsx",
            "raw_bucket":    "ecom-lakehouse-raw-dev",
            "archive_bucket":"ecom-lakehouse-archive-dev",
            "batch_id":      "orders-20250401-abc123",
            "dataset":       "orders",
            "env":           "dev"
        }

    Returns:
        {"archive_uri": "s3://ecom-lakehouse-archive-dev/orders/orders-20250401-abc123/..."}
    """
    # ------------------------------------------------------------------
    # Parse/generate missing keys from event or environment (robust auto-fill)
    # ------------------------------------------------------------------
    event = {**event}
    claim_payload = event.get("claimResult", {}).get("Payload")
    if isinstance(claim_payload, dict):
        for k, v in claim_payload.items():
            if k not in event or event[k] == "PLACEHOLDER_PARSED_FROM_KEY":
                event[k] = v

    if "raw_bucket" not in event:
        event["raw_bucket"] = event.get("source_bucket") or os.environ.get("RAW_BUCKET")

    if "env" not in event:
        event["env"] = os.environ.get("ENV") or os.environ.get("TF_ENV") or "dev"

    required_keys = ["file_key", "raw_bucket", "batch_id", "dataset", "env"]
    missing = [k for k in required_keys if k not in event]
    if missing:
        raise ValueError(f"archive_file: missing event keys: {missing}")

    file_key: str = event["file_key"]
    raw_bucket: str = event["raw_bucket"]
    batch_id: str = event["batch_id"]
    dataset: str = event["dataset"]
    env: str = event["env"]
    # archive_bucket is optional — derive from env when not provided
    archive_bucket: str = event.get("archive_bucket", f"ecom-lakehouse-archive-{env}")

    filename = os.path.basename(file_key)

    # ------------------------------------------------------------------
    # Construct archive destination key
    # Pattern: {dataset}/{batch_id}/{filename}
    # e.g.    "orders/orders-20250401-abc123/orders_apr_2025.xlsx"
    # ------------------------------------------------------------------
    archive_key = f"{dataset}/{batch_id}/{filename}"
    archive_uri = f"s3://{archive_bucket}/{archive_key}"

    logger.info(
        "Archiving: s3://%s/%s → %s",
        raw_bucket,
        file_key,
        archive_uri,
    )

    # ------------------------------------------------------------------
    # Step 1: Copy raw → archive
    # ------------------------------------------------------------------
    copy_source = {"Bucket": raw_bucket, "Key": file_key}
    try:
        s3_client.copy_object(
            CopySource=copy_source,
            Bucket=archive_bucket,
            Key=archive_key,
            # Preserve server-side encryption on the archive bucket
            ServerSideEncryption="aws:kms",
        )
        logger.info("Copy complete: %s", archive_uri)
    except Exception as exc:
        raise RuntimeError(
            f"archive_file: failed to copy s3://{raw_bucket}/{file_key} " f"to {archive_uri}: {exc}"
        ) from exc

    # ------------------------------------------------------------------
    # Step 2: Delete source key from raw zone
    # We delete AFTER a successful copy to ensure the archive is never lost.
    # ------------------------------------------------------------------
    try:
        s3_client.delete_object(Bucket=raw_bucket, Key=file_key)
        logger.info("Deleted source key: s3://%s/%s", raw_bucket, file_key)
    except Exception as exc:
        # Non-fatal: the archive is already written.  Log the failure and
        # continue — the ledger will be updated to ARCHIVED.  The duplicate
        # in raw zone will not be re-processed because the ledger already
        # shows ARCHIVED status for this file_key.
        logger.warning(
            "archive_file: failed to delete source key s3://%s/%s: %s "
            "(archive already written — continuing)",
            raw_bucket,
            file_key,
            exc,
        )

    # ------------------------------------------------------------------
    # Step 3: Update DynamoDB ledger → ARCHIVED
    # ------------------------------------------------------------------
    ledger = LedgerClient(env=env)
    ledger.mark_archived(file_key, archive_uri)

    logger.info("Archive complete: file_key=%s archive_uri=%s", file_key, archive_uri)
    return {"archive_uri": archive_uri}
