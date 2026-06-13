"""
logging_utils.py — Structured logging, metrics computation, and CloudWatch emission.

All Glue jobs and Lambda functions import get_logger() from here to ensure
consistent JSON-formatted log output that CloudWatch Logs Insights can query.

Metrics computed here (compute_metrics) feed:
  1. The gate() function — abort the batch if reject_rate > threshold.
  2. The ledger.mark_loaded() call — persist counts in DynamoDB.
  3. CloudWatch custom metrics (emit_cloudwatch_metric) — drives alarms.
"""

import json
import logging
import sys
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from lakehouse.config import PROJECT_PREFIX


# ---------------------------------------------------------------------------
# JSON log formatter
# ---------------------------------------------------------------------------
class _JsonFormatter(logging.Formatter):
    """
    Emit each log record as a single-line JSON object.

    Fields emitted:
      timestamp, level, name, message, plus any extra key=value pairs
      added via logger.info("msg", extra={"key": "value"}).
    """

    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
        }
        # Include any extra fields attached by the caller
        for key, value in record.__dict__.items():
            if key not in (
                "msg",
                "args",
                "levelname",
                "levelno",
                "pathname",
                "filename",
                "module",
                "exc_info",
                "exc_text",
                "stack_info",
                "lineno",
                "funcName",
                "created",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "processName",
                "process",
                "name",
                "message",
                "taskName",
            ):
                try:
                    json.dumps(value)  # only include JSON-serialisable extras
                    log_obj[key] = value
                except (TypeError, ValueError):
                    log_obj[key] = str(value)

        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_obj, default=str)


def get_logger(name: str) -> logging.Logger:
    """
    Return a Python logger configured with the JSON formatter.

    Idempotent: calling get_logger() multiple times with the same name
    returns the same logger without adding duplicate handlers.

    Args:
        name: Logger name — conventionally __name__ from the calling module.

    Returns:
        logging.Logger instance with a StreamHandler writing JSON to stdout.
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # already configured

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False  # avoid duplicate output in Glue runtime

    return logger


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------

def compute_metrics(
    df_in,
    df_valid,
    df_rejected,
    df_dedup_collapsed=None,
) -> dict:
    """
    Compute pipeline quality metrics from the four key DataFrames.

    Counts are materialised via DataFrame.count() — this triggers a Spark
    action so call this only once per pipeline run to minimise compute.

    Args:
        df_in:             DataFrame as read from staging (pre-validation).
        df_valid:          DataFrame after apply_rules() — valid rows only.
        df_rejected:       DataFrame of rejected rows (from apply_rules + RI checks).
        df_dedup_collapsed: Optional DataFrame before dedup to compute how many
                            duplicates were removed.  If None, dedup_collapsed=0.

    Returns:
        dict with keys:
          rows_in           — int
          rows_valid        — int
          rows_rejected     — int
          reject_rate       — float (0.0–1.0); 0.0 if rows_in == 0
          dedup_collapsed   — int (rows removed by within-batch dedup)
    """
    rows_in = df_in.count()
    rows_valid = df_valid.count()
    rows_rejected = df_rejected.count()
    reject_rate = rows_rejected / rows_in if rows_in > 0 else 0.0

    dedup_collapsed = 0
    if df_dedup_collapsed is not None:
        # df_dedup_collapsed is the DataFrame BEFORE dedup; df_valid is AFTER dedup.
        # The difference is the number of rows collapsed by the dedup step.
        rows_pre_dedup = df_dedup_collapsed.count()
        dedup_collapsed = max(0, rows_pre_dedup - rows_valid)

    metrics = {
        "rows_in": rows_in,
        "rows_valid": rows_valid,
        "rows_rejected": rows_rejected,
        "reject_rate": reject_rate,
        "dedup_collapsed": dedup_collapsed,
    }
    return metrics


# ---------------------------------------------------------------------------
# Quality gate
# ---------------------------------------------------------------------------

def gate(metrics: dict, threshold: float) -> None:
    """
    Raise ValueError if the batch reject_rate exceeds the allowed threshold.

    This is the circuit-breaker that prevents writing a heavily-corrupted
    batch to Delta.  When triggered:
      - The Glue job exits with a non-zero code.
      - Step Functions marks the state as FAILED.
      - An SNS alarm notifies the on-call engineer.
      - The quarantine zone already holds the rejected rows with reasons.

    Args:
        metrics:   Dict returned by compute_metrics().
        threshold: Maximum allowable reject_rate (e.g. 0.05 for 5%).

    Raises:
        ValueError: if reject_rate > threshold, with a descriptive message
                    showing the actual rate and threshold.
    """
    reject_rate = metrics.get("reject_rate", 0.0)
    rows_rejected = metrics.get("rows_rejected", 0)
    rows_in = metrics.get("rows_in", 0)

    if reject_rate > threshold:
        raise ValueError(
            f"Quality gate FAILED: reject_rate={reject_rate:.4f} "
            f"({rows_rejected}/{rows_in} rows) exceeds threshold={threshold:.4f}. "
            f"Inspect quarantine zone for rejected rows and reasons."
        )


# ---------------------------------------------------------------------------
# CloudWatch custom metrics
# ---------------------------------------------------------------------------

def emit_cloudwatch_metric(
    namespace: str,
    metric_name: str,
    value: float,
    dimensions: list,
    unit: str = "Count",
) -> None:
    """
    Publish a single custom metric data point to Amazon CloudWatch.

    Used by Glue jobs to emit rows_valid, rows_rejected, reject_rate etc.
    for alarming and dashboards.  Failures are logged but do not raise —
    a CloudWatch outage must not abort an otherwise successful data load.

    Args:
        namespace:   CloudWatch namespace, e.g. "ecom-lakehouse/ingestion".
        metric_name: Metric name, e.g. "rows_rejected".
        value:       Numeric value of the metric data point.
        dimensions:  List of dicts: [{"Name": "dataset", "Value": "orders"}, ...]
        unit:        CloudWatch unit string (default "Count").
                     Other valid values: "Bytes", "Milliseconds", "Percent", etc.
    """
    logger = get_logger(__name__)
    try:
        cw = boto3.client("cloudwatch", region_name="us-east-1")
        cw.put_metric_data(
            Namespace=namespace,
            MetricData=[
                {
                    "MetricName": metric_name,
                    "Dimensions": dimensions,
                    "Value": value,
                    "Unit": unit,
                }
            ],
        )
        logger.info(
            "CloudWatch metric emitted",
            extra={
                "namespace": namespace,
                "metric_name": metric_name,
                "value": value,
                "unit": unit,
            },
        )
    except ClientError as exc:
        # Non-fatal: log and continue
        logger.warning(
            "Failed to emit CloudWatch metric %s/%s: %s",
            namespace,
            metric_name,
            exc,
        )
