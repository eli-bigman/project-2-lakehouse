"""
transforms.py — Spark transformation functions for ecom-lakehouse.

All transformations operate on native Spark DataFrames (DynamicFrames are
PROHIBITED — ADR-019).  Each function is a pure transformation: it takes a
DataFrame and returns a new one without side effects, making them easy to
unit-test with local Spark.

Import convention:
    import pyspark.sql.functions as F
    from pyspark.sql import Window
"""

import pyspark.sql.functions as F
from pyspark.sql import DataFrame, Window
from pyspark.sql.types import StructType

from lakehouse.schemas import BUSINESS_COLS, SCHEMAS


def enforce_types(df: DataFrame, dataset: str) -> DataFrame:
    """
    Cast each column to its canonical Spark type defined in schemas.py.

    For each field in the canonical StructType, this function:
      1. Casts the column to the target type.
      2. For non-nullable fields, flags rows where the cast produced null
         (i.e., the source value could not be coerced).

    Returns the DataFrame with an additional boolean column "_cast_failed"
    that is True on any row where at least one required (nullable=False)
    column became null after casting.  Downstream validation.apply_rules()
    or ingest.py can filter on this flag and route those rows to quarantine.

    Note: nullable=True columns that cast to null are NOT flagged — null is a
    valid outcome for optional fields like days_since_prior_order.

    Args:
        df:      Input DataFrame (may have raw string types from Parquet).
        dataset: Short dataset name used to look up the canonical schema.

    Returns:
        DataFrame with columns cast to target types + "_cast_failed" boolean.
    """
    schema: StructType = SCHEMAS[dataset]

    # Audit columns (_ingest_ts etc.) do not yet exist at this stage;
    # only cast the fields that are present in the incoming staging data.
    audit_col_names = {"_ingest_ts", "_source_file", "_batch_id", "_record_hash"}

    cast_failed_conditions = []

    for field in schema.fields:
        if field.name in audit_col_names:
            continue  # audit cols added later by derive(); skip here

        if field.name not in df.columns:
            continue  # column absent in staging; will be flagged by validate_schema

        # Cast the column to the canonical type
        df = df.withColumn(field.name, F.col(field.name).cast(field.dataType))

        # Build a null-check condition for required (nullable=False) columns.
        # A cast that produces null on a required column means the source value
        # was not coercible — we want to quarantine those rows.
        if not field.nullable:
            cast_failed_conditions.append(F.col(field.name).isNull())

    # Combine all per-column null conditions into a single "_cast_failed" flag.
    if cast_failed_conditions:
        failed_expr = cast_failed_conditions[0]
        for cond in cast_failed_conditions[1:]:
            failed_expr = failed_expr | cond
        df = df.withColumn("_cast_failed", failed_expr)
    else:
        # No non-nullable columns found (should not happen with valid schemas)
        df = df.withColumn("_cast_failed", F.lit(False))

    return df


def derive(
    df: DataFrame,
    dataset: str,
    batch_id: str,
    source_file: str,
) -> DataFrame:
    """
    Add derived and audit columns to the DataFrame.

    Columns added:
      - order_date   (DateType): extracted from order_timestamp via to_date().
                                 Only added for fact tables (orders, order_items).
                                 dim_products has no timestamp — skipped for that
                                 dataset to avoid null/error.
      - _ingest_ts   (TimestampType): current_timestamp() at pipeline execution time.
      - _source_file (StringType): S3 key of the originating raw file.
      - _batch_id    (StringType): batch identifier from the Step Functions input.
      - _record_hash (StringType): SHA-256 of concat_ws("|", *BUSINESS_COLS).
                                   Used for MERGE no-op detection and deterministic
                                   dedup tie-breaking.  Excludes audit columns so
                                   the hash is stable across re-runs of the same data.

    Also handles the raw "date" column in xlsx fact files: the raw source has "date"
    which is dropped here after order_date is derived from order_timestamp.

    Args:
        df:          Input DataFrame after enforce_types().
        dataset:     Short dataset name.
        batch_id:    Batch identifier string.
        source_file: S3 URI of the original raw file.

    Returns:
        DataFrame with all derived and audit columns appended, and the raw "date"
        column dropped if present.
    """
    # Fact tables only: derive order_date from order_timestamp.
    # dim_products has no timestamp so this step is skipped for "products".
    if dataset in ("orders", "order_items"):
        df = df.withColumn("order_date", F.to_date(F.col("order_timestamp")))

        # Drop the raw "date" column if it survived from the Lambda Parquet
        # (xlsx files have a "date" column that is superseded by our derived column).
        if "date" in df.columns:
            df = df.drop("date")

    # Audit columns — governance trail on every row
    df = (
        df.withColumn("_ingest_ts", F.current_timestamp())
        .withColumn("_source_file", F.lit(source_file))
        .withColumn("_batch_id", F.lit(batch_id))
    )

    # _record_hash: deterministic SHA-256 of all business columns concatenated
    # with a pipe delimiter.  Using concat_ws avoids hash collisions that could
    # arise from simple concatenation (e.g., "ab"+"c" == "a"+"bc").
    # sha2(..., 256) returns a hex string; NULL inputs to concat_ws become empty
    # strings, so the hash is always non-null.
    business_cols = BUSINESS_COLS[dataset]
    hash_expr = F.sha2(
        F.concat_ws("|", *[F.col(c).cast("string") for c in business_cols]),
        256,
    )
    df = df.withColumn("_record_hash", hash_expr)

    return df


def dedup(df: DataFrame, merge_key: str) -> DataFrame:
    """
    Within-batch deduplication: keep exactly one row per merge_key value.

    Strategy (deterministic tie-break):
      1. Primary sort: latest _ingest_ts (most recently processed row wins).
      2. Secondary sort: highest _record_hash lexicographically (deterministic
         tie-break when two rows arrive in the same micro-batch and share the
         same timestamp precision).

    Why _record_hash as tie-break? Because _ingest_ts is a timestamp that can
    collide within the same Spark task execution window, especially in local
    test runs.  _record_hash is a SHA-256 hex string with 2^256 cardinality —
    collisions are astronomically unlikely.  Sorting descending and taking the
    first ensures we always pick the same row deterministically, making
    re-runs idempotent.

    Implementation uses a Window over the merge_key column, ranks by the
    two-column sort key, then filters rank == 1.

    Args:
        df:        DataFrame after derive() (must contain _ingest_ts and _record_hash).
        merge_key: Column name of the primary key / MERGE match key.

    Returns:
        DataFrame with at most one row per merge_key value.
    """
    window_spec = Window.partitionBy(merge_key).orderBy(
        F.col("_ingest_ts").desc(),  # primary: most recent first
        F.col("_record_hash").desc(),  # secondary: deterministic lexicographic tie-break
    )

    # IMPORTANT: use row_number(), NOT rank().
    # rank() assigns the same rank to ties, meaning exact duplicates (same merge key
    # + same _record_hash) would both get rank=1 and both survive, causing the Delta
    # MERGE to fail with "multiple source rows matched a single target row."
    # row_number() always assigns a unique sequential number — exactly one row
    # per merge_key value receives rn=1 regardless of ties.
    df_ranked = df.withColumn("_dedup_rn", F.row_number().over(window_spec))
    df_deduped = df_ranked.filter(F.col("_dedup_rn") == 1).drop("_dedup_rn")

    return df_deduped
