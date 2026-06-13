"""
src/ui/components/ledger.py — DynamoDB ledger and watermarks readers.
"""

from __future__ import annotations

from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

from ui import config


def _get_dynamodb_resource():
    """Return a DynamoDB resource, respecting the configured AWS profile."""
    if config.AWS_PROFILE:
        session = boto3.Session(profile_name=config.AWS_PROFILE, region_name=config.AWS_REGION)
    else:
        session = boto3.Session(region_name=config.AWS_REGION)
    return session.resource("dynamodb")


def get_recent_runs(n: int = 10) -> list[dict[str, Any]]:
    """
    Return the most recent n ingestion ledger entries from DynamoDB,
    ordered by ingest timestamp descending.

    Each entry is a dict matching the ledger schema:
      file_key, status, batch_id, dataset, ingest_ts, rows_in, rows_valid,
      rows_rejected, checksum.
    """
    dynamodb = _get_dynamodb_resource()
    table = dynamodb.Table(config.DYNAMODB_LEDGER_TABLE)

    # Scan and sort client-side; the ledger is small (one row per ingested file)
    response = table.scan()
    items = response.get("Items", [])

    # Handle pagination
    while "LastEvaluatedKey" in response:
        response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        items.extend(response.get("Items", []))

    # Sort by ingest_ts descending (latest first), return top n
    items.sort(key=lambda x: x.get("ingest_ts", ""), reverse=True)
    return items[:n]


def get_watermarks() -> list[dict[str, Any]]:
    """
    Return all watermark entries from DynamoDB.

    Each entry is a dict matching the watermarks schema:
      dataset, last_batch_id, last_processed_date, rows_valid, updated_at.
    """
    dynamodb = _get_dynamodb_resource()
    table = dynamodb.Table(config.DYNAMODB_WATERMARKS_TABLE)

    response = table.scan()
    items = response.get("Items", [])

    while "LastEvaluatedKey" in response:
        response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        items.extend(response.get("Items", []))

    return items
