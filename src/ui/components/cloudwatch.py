"""
src/ui/components/cloudwatch.py — CloudWatch metric history helper.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import boto3
import pandas as pd

from ui import config


def _get_cw_client():
    """Return a CloudWatch client, respecting the configured AWS profile."""
    if config.AWS_PROFILE:
        session = boto3.Session(profile_name=config.AWS_PROFILE, region_name=config.AWS_REGION)
    else:
        session = boto3.Session(region_name=config.AWS_REGION)
    return session.client("cloudwatch")


def get_metric_history(
    namespace: str,
    metric_name: str,
    dimensions: list[dict[str, str]],
    days: int = 7,
    period: int = 86400,
    stat: str = "Sum",
) -> pd.DataFrame:
    """
    Retrieve CloudWatch metric datapoints for the last `days` days.

    Parameters
    ----------
    namespace:   CloudWatch metric namespace, e.g. "ecom-lakehouse/pipeline".
    metric_name: Metric name, e.g. "rows_valid", "rows_rejected", "rows_in".
    dimensions:  List of dimension dicts, e.g. [{"Name": "dataset", "Value": "fct_orders"}].
    days:        Number of days of history to retrieve (default: 7).
    period:      Aggregation period in seconds (default: 86400 = 1 day).
    stat:        CloudWatch statistic (default: "Sum").

    Returns
    -------
    pandas.DataFrame with columns: Timestamp, Value.
    Sorted ascending by Timestamp.
    """
    cw = _get_cw_client()

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=days)

    response = cw.get_metric_statistics(
        Namespace=namespace,
        MetricName=metric_name,
        Dimensions=dimensions,
        StartTime=start_time,
        EndTime=end_time,
        Period=period,
        Statistics=[stat],
    )

    datapoints = response.get("Datapoints", [])
    if not datapoints:
        return pd.DataFrame(columns=["Timestamp", "Value"])

    df = pd.DataFrame(datapoints)[["Timestamp", stat]].rename(columns={stat: "Value"})
    df.sort_values("Timestamp", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df
