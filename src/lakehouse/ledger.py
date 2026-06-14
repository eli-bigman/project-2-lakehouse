"""
ledger.py — DynamoDB ingestion ledger and watermarks client.

This module encapsulates all DynamoDB access for the ecom-lakehouse control plane.
Two tables are managed (architecture.md §3.6):

  ecom_lakehouse_ingestion_ledger_{env}
    PK: file_key (string) — the S3 raw key, e.g. "orders/2025/04/orders_apr_2025.xlsx"
    Tracks: status (CLAIMED|NORMALIZED|LOADED|ARCHIVED|FAILED), checksum,
            dataset, batch_id, staging_uri, row counts, reject_rate, archive_uri.

  ecom_lakehouse_watermarks_{env}
    PK: dataset (string) — short dataset name
    Tracks: last_period (YYYYMM), last_batch_id, updated_at.

Idempotency pattern (conditional writes):
  claim_file() uses a DynamoDB ConditionExpression:
    attribute_not_exists(file_key) OR #status IN (:failed)
  This means:
    - A file that has never been seen → claim succeeds.
    - A file in FAILED status → retry is allowed (re-claim succeeds).
    - A file in any other status (NORMALIZED/LOADED/ARCHIVED) → claim fails,
      returning False — the caller skips processing (already done or in-flight).

  This prevents double-processing when Step Functions retries a Lambda invocation
  or when the pipeline is manually re-triggered for a file already in flight.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class LedgerClient:
    """
    Client for the DynamoDB ingestion ledger and watermarks tables.

    Usage:
        client = LedgerClient(env="dev")
        claimed = client.claim_file(file_key, batch_id, dataset, checksum, staging_uri)
        if not claimed:
            return  # already processed — skip
        ...
        client.mark_loaded(file_key, metrics_dict)
    """

    def __init__(self, env: str):
        """
        Initialise DynamoDB resource and resolve table names for the given env.

        Args:
            env: Environment suffix ("dev" | "prod").
        """
        self.env = env
        self.dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        self.ledger_table = self.dynamodb.Table(f"ecom_lakehouse_ingestion_ledger_{env}")
        self.watermarks_table = self.dynamodb.Table(f"ecom_lakehouse_watermarks_{env}")

    def _now_iso(self) -> str:
        """Return current UTC timestamp as ISO-8601 string."""
        return datetime.now(timezone.utc).isoformat()

    def claim_file(
        self,
        file_key: str,
        batch_id: str,
        dataset: str,
        checksum: str,
        staging_uri: str,
    ) -> bool:
        """
        Atomically claim a file for processing via a DynamoDB conditional put.

        The condition:
            attribute_not_exists(file_key) OR #s IN (:failed)
        ensures that:
          - New files are claimed unconditionally.
          - FAILED files may be re-claimed (retry on error).
          - Files in any other state (NORMALIZED, LOADED, ARCHIVED, CLAIMED)
            are rejected — the pipeline is already handling them.

        Args:
            file_key:    S3 raw key, e.g. "orders/2025/04/orders_apr_2025.xlsx".
            batch_id:    Batch identifier stamped at pipeline entry.
            dataset:     Short dataset name ("products" | "orders" | "order_items").
            checksum:    MD5 hex digest of the raw S3 object body.
            staging_uri: Placeholder staging S3 URI (set before normalization).

        Returns:
            True if the claim was successfully written (proceed with processing).
            False if a ConditionalCheckFailedException was raised (file already
            in-flight or successfully processed — skip).
        """
        item = {
            "file_key": file_key,
            "batch_id": batch_id,
            "dataset": dataset,
            "checksum": checksum,
            "staging_uri": staging_uri,
            "status": "CLAIMED",
            "claimed_at": self._now_iso(),
        }
        try:
            self.ledger_table.put_item(
                Item=item,
                ConditionExpression=("attribute_not_exists(file_key) OR #s = :failed"),
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":failed": "FAILED"},
            )
            logger.info("Ledger: claimed %s (batch_id=%s)", file_key, batch_id)
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                logger.info("Ledger: %s already claimed/processed — skipping", file_key)
                return False
            raise

    def mark_normalized(self, file_key: str, rows_in: int) -> None:
        """
        Update ledger status to NORMALIZED after the Lambda Parquet conversion.

        Args:
            file_key: S3 raw key identifying the ledger row.
            rows_in:  Number of rows read from the source file.
        """
        self.ledger_table.update_item(
            Key={"file_key": file_key},
            UpdateExpression=("SET #s = :status, rows_in = :rows_in, normalized_at = :ts"),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":status": "NORMALIZED",
                ":rows_in": rows_in,
                ":ts": self._now_iso(),
            },
        )
        logger.info("Ledger: NORMALIZED %s (rows_in=%d)", file_key, rows_in)

    def mark_loaded(self, file_key: str, metrics_dict: dict) -> None:
        """
        Update ledger status to LOADED with row-count metrics from the Glue job.

        Expected keys in metrics_dict:
            rows_in       — total rows from staging Parquet
            rows_valid    — rows that passed all validation rules
            rows_rejected — rows sent to quarantine
            reject_rate   — rows_rejected / rows_in (float, 0–1)

        Args:
            file_key:     S3 raw key identifying the ledger row.
            metrics_dict: Dict returned by logging_utils.compute_metrics().
        """
        self.ledger_table.update_item(
            Key={"file_key": file_key},
            UpdateExpression=(
                "SET #s = :status, "
                "rows_valid = :rows_valid, "
                "rows_rejected = :rows_rejected, "
                "reject_rate = :reject_rate, "
                "loaded_at = :ts"
            ),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":status": "LOADED",
                ":rows_valid": metrics_dict.get("rows_valid", 0),
                ":rows_rejected": metrics_dict.get("rows_rejected", 0),
                ":reject_rate": str(metrics_dict.get("reject_rate", 0.0)),
                ":ts": self._now_iso(),
            },
        )
        logger.info(
            "Ledger: LOADED %s (rows_valid=%d, rows_rejected=%d, reject_rate=%.4f)",
            file_key,
            metrics_dict.get("rows_valid", 0),
            metrics_dict.get("rows_rejected", 0),
            metrics_dict.get("reject_rate", 0.0),
        )

    def mark_archived(self, file_key: str, archive_uri: str) -> None:
        """
        Update ledger status to ARCHIVED and record the archive S3 URI.

        Args:
            file_key:    S3 raw key identifying the ledger row.
            archive_uri: Full S3 URI where the original file was archived,
                         e.g. s3://ecom-lakehouse-archive-dev/orders/batch_id=.../
        """
        self.ledger_table.update_item(
            Key={"file_key": file_key},
            UpdateExpression=("SET #s = :status, archive_uri = :uri, archived_at = :ts"),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":status": "ARCHIVED",
                ":uri": archive_uri,
                ":ts": self._now_iso(),
            },
        )
        logger.info("Ledger: ARCHIVED %s → %s", file_key, archive_uri)

    def mark_failed(self, file_key: str, error_msg: str) -> None:
        """
        Update ledger status to FAILED with the error message.

        FAILED rows may be re-claimed by a subsequent pipeline run
        (see claim_file() conditional expression).

        Args:
            file_key:  S3 raw key identifying the ledger row.
            error_msg: Human-readable error message (truncated to 4 KB to stay
                       within DynamoDB attribute limits).
        """
        # DynamoDB String attributes have a 400 KB limit; truncate to be safe.
        truncated_msg = error_msg[:4000] if len(error_msg) > 4000 else error_msg
        self.ledger_table.update_item(
            Key={"file_key": file_key},
            UpdateExpression=("SET #s = :status, error_msg = :msg, failed_at = :ts"),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":status": "FAILED",
                ":msg": truncated_msg,
                ":ts": self._now_iso(),
            },
        )
        logger.error("Ledger: FAILED %s — %s", file_key, truncated_msg)

    def update_watermark(
        self,
        dataset: str,
        period: str,
        batch_id: str,
    ) -> None:
        """
        Update the watermarks table with the last successfully processed period.

        The watermarks table is used to detect new monthly drops: the Step
        Functions orchestrator compares the incoming file's period against the
        stored watermark to avoid re-processing already-loaded months.

        Args:
            dataset:  Short dataset name ("products" | "orders" | "order_items").
            period:   Processing period string, e.g. "202504" (YYYYMM).
            batch_id: Batch identifier of the successful load.
        """
        self.watermarks_table.put_item(
            Item={
                "dataset": dataset,
                "last_period": period,
                "last_batch_id": batch_id,
                "updated_at": self._now_iso(),
            }
        )
        logger.info("Watermark: updated %s → period=%s batch_id=%s", dataset, period, batch_id)

    def get_watermark(self, dataset: str) -> Optional[dict]:
        """
        Retrieve the last-processed watermark for a dataset.

        Args:
            dataset: Short dataset name.

        Returns:
            Dict with keys {dataset, last_period, last_batch_id, updated_at},
            or None if no watermark exists (first run).
        """
        response = self.watermarks_table.get_item(Key={"dataset": dataset})
        item = response.get("Item")
        if item is None:
            logger.info("Watermark: no entry found for dataset=%s", dataset)
        return item
