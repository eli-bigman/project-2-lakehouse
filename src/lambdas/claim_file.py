"""
claim_file.py — Lambda: claim a raw file in the DynamoDB ingestion ledger.

This is the first Lambda invoked by the Step Functions workflow after a new
file lands in the raw S3 zone.  Its purpose is to atomically claim the file
for processing so that concurrent pipeline executions (or Step Functions
automatic retries) do not process the same file twice.

Idempotency guarantee:
  LedgerClient.claim_file() uses a DynamoDB conditional write:
    attribute_not_exists(file_key) OR status = "FAILED"
  If the file is already claimed/normalized/loaded/archived, the call returns
  False and this Lambda returns {already_processed: True}.  Step Functions
  should wire a Choice state on that flag to skip the rest of the pipeline.

Checksum computation:
  The MD5 checksum of the raw S3 object is computed here (before any
  transformation) so that:
    1. It can be stored in the ledger as a fingerprint of the original file.
    2. Future runs of the same batch_id can verify they are operating on
       the same bytes (guards against S3 object mutation or re-upload).
"""

import hashlib
import logging
from typing import Any, Dict

import boto3

from lakehouse.config import PROJECT_PREFIX
from lakehouse.ledger import LedgerClient

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

s3_client = boto3.client("s3")


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda entry point: claim file in the DynamoDB ingestion ledger.

    Event shape:
        {
            "file_key":   "orders/2025/04/orders_apr_2025.xlsx",
            "dataset":    "orders",
            "batch_id":   "orders-20250401-abc123",
            "raw_bucket": "ecom-lakehouse-raw-dev",
            "env":        "dev"
        }

    Returns:
        If already processed:
            {"already_processed": True}
        If newly claimed:
            {
                "already_processed": False,
                "batch_id":          "orders-20250401-abc123",
                "checksum":          "<md5_hex>",
                "staging_uri":       "s3://ecom-lakehouse-staging-dev/orders/batch_id=.../",
                "file_key":          "orders/2025/04/orders_apr_2025.xlsx",
                "dataset":           "orders"
            }
    """
    # ------------------------------------------------------------------
    # Parse/generate missing keys from event or environment (robust auto-fill)
    # ------------------------------------------------------------------
    import datetime
    import os

    file_key: str = event.get("file_key")
    if not file_key:
        raise ValueError("claim_file: missing required key 'file_key' in event")

    raw_bucket: str = (
        event.get("raw_bucket") or event.get("source_bucket") or os.environ.get("RAW_BUCKET")
    )
    if not raw_bucket:
        raise ValueError("claim_file: could not resolve 'raw_bucket' from event or environment")

    env: str = event.get("env") or os.environ.get("ENV") or os.environ.get("TF_ENV") or "dev"

    dataset: str = event.get("dataset")
    if not dataset or dataset == "PLACEHOLDER_PARSED_FROM_KEY":
        # Parse from file_key, e.g. "dim_products/2025/04/products.csv" -> "products"
        parts = file_key.split("/")
        if parts:
            first_dir = parts[0]
            if first_dir.startswith("dim_"):
                dataset = first_dir[4:]
            elif first_dir.startswith("fct_"):
                dataset = first_dir[4:]
            else:
                dataset = first_dir
        else:
            raise ValueError(f"claim_file: could not parse dataset from file_key '{file_key}'")

    # Normalize dataset to valid short dataset name (e.g. products, orders, order_items)
    if dataset not in ["products", "orders", "order_items"]:
        clean_ds = dataset.replace("-", "_")
        if clean_ds == "dim_products":
            dataset = "products"
        elif clean_ds == "fct_orders":
            dataset = "orders"
        elif clean_ds == "fct_order_items":
            dataset = "order_items"
        else:
            raise ValueError(
                f"claim_file: invalid dataset '{dataset}' resolved from file_key '{file_key}'"
            )

    batch_id: str = event.get("batch_id")
    if not batch_id:
        # Construct a descriptive, unique batch ID
        event_id = event.get("event_id", "manual")
        clean_event_id = event_id.replace("-", "")[:8]
        timestamp = datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S")
        batch_id = f"{dataset}-{timestamp}-{clean_event_id}"

    logger.info(
        "Claiming file: bucket=%s key=%s dataset=%s batch_id=%s",
        raw_bucket,
        file_key,
        dataset,
        batch_id,
    )

    # ------------------------------------------------------------------
    # Compute MD5 checksum of the raw S3 object
    # We stream the object body in chunks to avoid loading large files
    # entirely into Lambda memory.
    # ------------------------------------------------------------------
    try:
        response = s3_client.get_object(Bucket=raw_bucket, Key=file_key)
        md5 = hashlib.md5()
        for chunk in response["Body"].iter_chunks(chunk_size=8 * 1024 * 1024):
            md5.update(chunk)
        checksum = md5.hexdigest()
    except Exception as exc:
        raise RuntimeError(
            f"claim_file: failed to read s3://{raw_bucket}/{file_key} for checksum: {exc}"
        ) from exc

    logger.info("Checksum computed: %s", checksum)

    # ------------------------------------------------------------------
    # Construct the staging URI placeholder
    # The actual Parquet file is written by normalize_to_parquet Lambda;
    # we store the prefix here so the ledger has a reference from the start.
    # ------------------------------------------------------------------
    staging_bucket = f"{PROJECT_PREFIX}-staging-{env}"
    staging_uri = f"s3://{staging_bucket}/{dataset}/batch_id={batch_id}/"

    # ------------------------------------------------------------------
    # Attempt to claim the file in DynamoDB
    # ------------------------------------------------------------------
    ledger = LedgerClient(env=env)
    claimed = ledger.claim_file(
        file_key=file_key,
        batch_id=batch_id,
        dataset=dataset,
        checksum=checksum,
        staging_uri=staging_uri,
    )

    if not claimed:
        logger.info("File already processed — returning already_processed=True for %s", file_key)
        return {"already_processed": True}

    logger.info("File claimed successfully: file_key=%s batch_id=%s", file_key, batch_id)
    return {
        "already_processed": False,
        "batch_id": batch_id,
        "checksum": checksum,
        "staging_uri": staging_uri,
        "file_key": file_key,
        "dataset": dataset,
    }
