"""
merge.py — Delta Lake MERGE (upsert), OPTIMIZE, and VACUUM logic.

This module implements the Silver-zone write strategy:
  - First run:  write the full DataFrame as a new Delta table.
  - Subsequent runs: MERGE with matched-update (only when _record_hash differs)
                     + not-matched-insert.

The no-op update skip (updating only when _record_hash differs) is critical:
  - It prevents Delta from writing new Parquet files for rows whose business
    data has not changed, keeping the transaction log and file count small.
  - It makes re-runs idempotent: re-processing the same source file produces
    exactly zero net writes to the Delta table.

MERGE predicate hint for fact tables:
  Adding a time-range condition on order_date to the MERGE ON clause allows
  Delta to skip data files outside that range using data skipping statistics.
  Without this hint Delta must scan all files to find matching keys, which
  degrades as the table grows.  For dim_products (no order_date) no hint is added.

DynamicFrames are PROHIBITED (ADR-019) — this module uses DataFrames + DeltaTable API.
"""

from pyspark.sql import SparkSession, DataFrame
import pyspark.sql.functions as F


def upsert(
    spark: SparkSession,
    df: DataFrame,
    target_path: str,
    merge_key: str,
) -> dict:
    """
    Write df to a Delta table at target_path using MERGE semantics.

    Behaviour:
      - If the Delta table does not yet exist at target_path, create it by
        writing df directly (first-run bootstrap).
      - Otherwise, perform a Delta MERGE:
          WHEN MATCHED AND source._record_hash != target._record_hash → UPDATE ALL
          WHEN NOT MATCHED → INSERT ALL
        The "AND hash differs" condition means unchanged rows produce no I/O.

    MERGE predicate hint: for fact tables (orders, order_items) we add an
    additional filter on order_date so Delta's data skipping can prune files
    that don't overlap the incoming batch's date range.  This is derived from
    the min/max order_date in the incoming DataFrame rather than hard-coded.

    Args:
        spark:       Active SparkSession.
        df:          Validated, deduped DataFrame to upsert.
        target_path: Full S3 URI of the Delta table,
                     e.g. s3://ecom-lakehouse-dwh-dev/fct_orders/
        merge_key:   Column name for the MERGE ON predicate (e.g. "order_id").

    Returns:
        dict with keys: {"action": "created"|"merged", "target_path": str}
    """
    from delta.tables import DeltaTable
    from lakehouse import io as lake_io

    if not lake_io.delta_table_exists(spark, target_path):
        # First run: create the Delta table by writing the full DataFrame.
        # mode="overwrite" with Delta creates the table atomically.
        df.write.format("delta").mode("overwrite").save(target_path)
        return {"action": "created", "target_path": target_path}

    # Subsequent runs: MERGE
    delta_table = DeltaTable.forPath(spark, target_path)

    # Build the base MERGE ON condition (primary-key equality)
    merge_condition = f"target.{merge_key} = source.{merge_key}"

    # MERGE predicate hint: if order_date exists in the incoming DataFrame,
    # add a time-range filter so Delta can skip files outside the batch window.
    # This leverages Delta's min/max statistics per file for data skipping.
    if "order_date" in df.columns:
        date_bounds = df.agg(
            F.min("order_date").alias("min_date"),
            F.max("order_date").alias("max_date"),
        ).collect()[0]
        min_date = date_bounds["min_date"]
        max_date = date_bounds["max_date"]
        if min_date is not None and max_date is not None:
            merge_condition += (
                f" AND target.order_date >= '{min_date}'"
                f" AND target.order_date <= '{max_date}'"
            )

    (
        delta_table.alias("target")
        .merge(df.alias("source"), merge_condition)
        # Update only when the business-column hash has changed (no-op skip).
        # This is the key optimisation: unchanged rows produce zero file writes.
        .whenMatchedUpdateAll(condition="source._record_hash != target._record_hash")
        .whenNotMatchedInsertAll()
        .execute()
    )

    return {"action": "merged", "target_path": target_path}


def run_optimize(
    spark: SparkSession,
    table_path: str,
    zorder_cols: list,
) -> None:
    """
    Run OPTIMIZE ZORDER BY on a Delta table to compact small files and
    cluster data by the most-filtered columns.

    OPTIMIZE should be called after each successful MERGE to:
      1. Compact the small Parquet files produced by the MERGE into larger,
         more efficient files (reduces Athena scan cost and latency).
      2. Re-cluster data by the Z-ORDER columns so that file-level statistics
         improve over time, allowing Delta to skip more files on predicate pushdown.

    Z-ORDER columns per table (config.ZORDER_COLS):
      - dim_products:    department
      - fct_orders:      order_date
      - fct_order_items: order_date, product_id

    OPTIMIZE is idempotent: running it again on an already-optimized table
    that hasn't changed is a no-op.

    Args:
        spark:       Active SparkSession.
        table_path:  Full S3 URI of the Delta table.
        zorder_cols: List of column names for ZORDER BY clause.
    """
    import logging
    logger = logging.getLogger(__name__)

    zorder_clause = ", ".join(zorder_cols)
    optimize_sql = (
        f"OPTIMIZE delta.`{table_path}` ZORDER BY ({zorder_clause})"
    )
    logger.info("Running OPTIMIZE: %s", optimize_sql)

    result = spark.sql(optimize_sql)
    result.show(truncate=False)
    logger.info("OPTIMIZE complete for %s", table_path)


def run_vacuum(
    spark: SparkSession,
    table_path: str,
    retain_hours: int = 168,
) -> None:
    """
    Run VACUUM on a Delta table to remove obsolete Parquet files.

    VACUUM removes files that are no longer referenced by the current Delta
    transaction log AND are older than the retention window.  The default
    retention of 168 hours (7 days) gives a safety window for:
      - Time-travel queries going back up to 7 days.
      - Long-running Athena queries that might still hold references to
        older snapshot files.

    Do NOT set retain_hours < 168 in production: Delta raises an error by
    default to prevent accidental deletion of live data.  To override the
    safety check you would need to set:
        spark.databricks.delta.retentionDurationCheck.enabled=false
    which we do NOT do here.

    Args:
        spark:        Active SparkSession.
        table_path:   Full S3 URI of the Delta table.
        retain_hours: Retention window in hours (default 168 = 7 days).
    """
    import logging
    logger = logging.getLogger(__name__)

    vacuum_sql = f"VACUUM delta.`{table_path}` RETAIN {retain_hours} HOURS"
    logger.info("Running VACUUM: %s", vacuum_sql)

    spark.sql(vacuum_sql)
    logger.info("VACUUM complete for %s (retain %d hours)", table_path, retain_hours)
