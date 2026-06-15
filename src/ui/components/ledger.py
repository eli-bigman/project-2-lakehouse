"""
src/ui/components/ledger.py — DynamoDB ledger and watermarks readers.
"""

from __future__ import annotations

from typing import Any

import boto3

from ui import config


def _get_dynamodb_resource():
    """Return a DynamoDB resource using the configured region and optional profile."""
    if config.AWS_PROFILE:
        session = boto3.Session(profile_name=config.AWS_PROFILE, region_name=config.AWS_REGION)
    else:
        session = boto3.Session(region_name=config.AWS_REGION)
    return session.resource("dynamodb")


def get_recent_runs(n: int = 10) -> list[dict[str, Any]]:
    """
    Return the most recent n ingestion ledger entries from DynamoDB,
    ordered by ingest timestamp descending.
    """
    dynamodb = _get_dynamodb_resource()
    table = dynamodb.Table(config.DYNAMODB_LEDGER_TABLE)

    response = table.scan()
    items = response.get("Items", [])

    while "LastEvaluatedKey" in response:
        response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        items.extend(response.get("Items", []))

    items.sort(key=lambda x: x.get("ingest_ts", ""), reverse=True)
    return items[:n]


def get_watermarks() -> list[dict[str, Any]]:
    """
    Return all watermark entries from DynamoDB.
    """
    dynamodb = _get_dynamodb_resource()
    table = dynamodb.Table(config.DYNAMODB_WATERMARKS_TABLE)

    response = table.scan()
    items = response.get("Items", [])

    while "LastEvaluatedKey" in response:
        response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        items.extend(response.get("Items", []))

    return items
