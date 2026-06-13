"""
io.py — Spark I/O helpers for ecom-lakehouse.

All read/write operations against S3 (staging, quarantine) go through
these helpers so that path construction, format, and schema enforcement
are consistent across every Glue job.

DynamicFrames are PROHIBITED (ADR-019) — this module uses native Spark
DataFrames exclusively.
"""

from pyspark.sql import SparkSession, DataFrame
from pyspark.sql.types import StructType

from lakehouse import config


def delta_session(app_name: str, enable_hive_catalog: bool = False) -> SparkSession:
    """
    Create and return a SparkSession with Delta Lake extensions configured.

    Configuration applied:
      - spark.sql.extensions: registers DeltaSparkSessionExtension so that
        SQL commands like OPTIMIZE, VACUUM, and DESCRIBE HISTORY work.
      - spark.sql.catalog.spark_catalog: replaces the default catalog with
        DeltaCatalog so that Delta tables are resolved by path or by Glue
        Catalog name seamlessly.

    Glue managed environments typically inject their own SparkSession; calling
    SparkSession.builder.getOrCreate() inside a Glue job will return the
    pre-configured session, but the Delta extensions must still be declared
    via spark.conf.set if the session is already running.  We set them on
    the builder here; in a Glue job context this call is effectively a no-op
    for options already set by the runtime.

    Args:
        app_name:           Application / job name surfaced in Spark UI.
        enable_hive_catalog: Set True to include Glue Catalog as Hive metastore
                             (only needed when registering tables by name, not path).

    Returns:
        SparkSession with Delta extensions.
    """
    builder = (
        SparkSession.builder.appName(app_name)
        .config(
            "spark.sql.extensions",
            "io.delta.sql.DeltaSparkSessionExtension",
        )
        .config(
            "spark.sql.catalog.spark_catalog",
            "org.apache.spark.sql.delta.catalog.DeltaCatalog",
        )
    )

    if enable_hive_catalog:
        # Enable Glue Data Catalog as the Hive metastore so that CREATE TABLE
        # statements register tables accessible via Athena (ADR-015).
        builder = builder.config(
            "spark.hadoop.hive.metastore.client.factory.class",
            "com.amazonaws.glue.catalog.metastore.AWSGlueDataCatalogHiveClientFactory",
        )

    spark = builder.getOrCreate()

    # Ensure Delta write defaults match our design contract:
    #   - overwriteSchema=false  → schema changes must go through code review
    #   - autoMerge=false        → no silent schema evolution (ADR-005)
    spark.conf.set("spark.databricks.delta.schema.autoMerge.enabled", "false")

    return spark


def read_staging(spark: SparkSession, staging_uri: str, schema: StructType) -> DataFrame:
    """
    Read normalized Parquet from the staging zone WITHOUT enforcing types at read time.

    Why permissive reading?
      The staging Parquet is produced by the Lambda normalizer via pandas:
        - products.csv is read with dtype=str  → all columns are strings in Parquet.
        - xlsx files give numeric columns physical types (int64, float64) that may
          not directly match Spark canonical types (e.g. DecimalType(10,2)).
      Spark's vectorized Parquet reader does NOT auto-cast string→int or
      double→decimal at read time — supplying a typed schema at this stage would
      raise SchemaColumnConvertNotSupportedException.

      Instead, we read with Spark's inferred schema (physical Parquet types) and
      delegate all type coercions to transforms.enforce_types(), which uses safe
      Spark Column.cast() operations and flags cast failures.

      The `schema` argument is accepted but ignored at read time; it is kept in
      the signature so that callers can still pass the canonical schema for
      documentation / IDE type-checking purposes.

    Column presence note:
      For fact tables (orders, order_items) the staging Parquet contains the raw
      "date" column from the xlsx source, NOT "order_date".  transforms.derive()
      re-derives order_date from order_timestamp and drops "date" — the read here
      preserves that raw column faithfully.

    Args:
        spark:       Active SparkSession.
        staging_uri: Full S3 URI to the staging prefix,
                     e.g. s3://ecom-lakehouse-staging-dev/orders/batch_id=abc123/
        schema:      Canonical StructType (accepted for documentation; not applied
                     at read time — see rationale above).

    Returns:
        DataFrame with Spark-inferred physical types from the Parquet metadata.
        All business columns are present; no audit columns yet.
    """
    df = (
        spark.read.format("parquet")
        .option("mergeSchema", "false")  # single-file staging; no schema merge needed
        .load(staging_uri)
    )
    return df


def write_quarantine(
    df: DataFrame,
    dataset: str,
    batch_id: str,
    quarantine_bucket: str,
    env: str,
) -> str:
    """
    Write rejected rows (with reject_reason column) to the quarantine zone.

    Path pattern: s3://{quarantine_bucket}/{dataset}/batch_id={batch_id}/
    The Parquet is written in a single partition (coalesce(1)) because quarantine
    files are small and are read by humans/BI tools infrequently — small-file
    penalty is acceptable here.

    Args:
        df:                 DataFrame of rejected rows; must contain a
                            "reject_reason" column added by validation.apply_rules().
        dataset:            Short dataset name ("products" | "orders" | "order_items").
        batch_id:           Batch identifier threaded from the Step Functions input.
        quarantine_bucket:  Name of the quarantine S3 bucket (not full URI).
        env:                Environment suffix ("dev" | "prod").

    Returns:
        Full S3 URI where rejected rows were written.
    """
    quarantine_uri = (
        f"s3://{quarantine_bucket}/{dataset}/batch_id={batch_id}/"
    )
    (
        df.coalesce(1)
        .write.format("parquet")
        .mode("overwrite")   # idempotent: re-run overwrites previous quarantine drop
        .save(quarantine_uri)
    )
    return quarantine_uri


def delta_table_exists(spark: SparkSession, path: str) -> bool:
    """
    Return True if a Delta table already exists at the given S3 path.

    Used by merge.upsert() to decide whether to CREATE (first run) or MERGE
    (subsequent runs).  Also used by validation.referential_integrity() to
    guard FK checks on first load — the dim_products or fct_orders table may
    not yet exist when order_items runs for the first time.

    Implementation: Delta's _delta_log directory is the authoritative signal.
    Attempting to read with format("delta") would raise an AnalysisException
    if the table does not exist, so we use the DeltaTable.isDeltaTable() API
    which is safe and does not throw.

    Args:
        spark: Active SparkSession.
        path:  Full S3 URI, e.g. s3://ecom-lakehouse-dwh-dev/dim_products/

    Returns:
        True if a Delta log exists at path; False otherwise.
    """
    from delta.tables import DeltaTable  # local import — delta not available at plan time

    return DeltaTable.isDeltaTable(spark, path)
