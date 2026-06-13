"""
Unit tests for src/lakehouse/transforms.py

Actual API (matched to the implemented signatures):

    enforce_types(df, dataset) -> DataFrame
        dataset is the SHORT name: "products" | "orders" | "order_items".
        Adds _cast_failed (bool) column; True on rows where a non-nullable
        column became null after casting.

    derive(df, dataset, batch_id, source_file) -> DataFrame
        Appends _ingest_ts, _source_file, _batch_id, _record_hash.
        For "orders" and "order_items", also derives order_date = date(order_timestamp).

    dedup(df, merge_key: str) -> DataFrame
        merge_key is the column name string, e.g. "product_id", "order_id", "id".
        Keeps one row per merge_key value; latest _ingest_ts wins, then highest
        _record_hash as deterministic tie-break.
"""

import datetime

from pyspark.sql import functions as F
from pyspark.sql.types import (
    IntegerType,
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

from lakehouse.transforms import dedup, derive, enforce_types


# ---------------------------------------------------------------------------
# enforce_types
# ---------------------------------------------------------------------------


class TestEnforceTypes:
    def test_enforce_types_valid_products(self, spark, sample_products_df):
        """Clean product rows should produce no _cast_failed=True rows."""
        result = enforce_types(sample_products_df, dataset="products")

        assert "_cast_failed" in result.columns, "enforce_types must add _cast_failed column"
        failed_count = result.filter(F.col("_cast_failed") == True).count()  # noqa: E712
        assert failed_count == 0, f"Expected 0 cast failures on clean data, got {failed_count}"

    def test_enforce_types_null_pk_flagged(self, spark):
        """
        A row with null product_id must produce _cast_failed=True.
        Null PKs cannot satisfy the non-nullable PK constraint — enforce_types detects them.
        """
        schema = StructType(
            [
                StructField("product_id", IntegerType(), True),
                StructField("department_id", IntegerType(), True),
                StructField("department", StringType(), True),
                StructField("product_name", StringType(), True),
            ]
        )
        data = [(None, 1, "Books", "No PK Row")]
        df = spark.createDataFrame(data, schema=schema)

        result = enforce_types(df, dataset="products")

        assert "_cast_failed" in result.columns
        flagged = result.filter(F.col("_cast_failed") == True)  # noqa: E712
        assert flagged.count() == 1, "Null PK row must be flagged with _cast_failed=True"


# ---------------------------------------------------------------------------
# derive
# ---------------------------------------------------------------------------


class TestDerive:
    def test_derive_adds_audit_columns(self, spark, sample_products_df):
        """
        After derive(), the DataFrame must contain the four audit columns:
        _ingest_ts, _source_file, _batch_id, _record_hash.
        """
        result = derive(
            sample_products_df,
            dataset="products",
            batch_id="products-20250401-abc123",
            source_file="s3://bucket/products.csv",
        )

        for col in ("_ingest_ts", "_source_file", "_batch_id", "_record_hash"):
            assert col in result.columns, f"Expected column '{col}' after derive()"

    def test_derive_source_file_and_batch_id_values(self, spark, sample_products_df):
        """_source_file and _batch_id must equal the literal values passed in."""
        batch = "products-20250401-xyz"
        src = "s3://ecom-lakehouse-staging-dev/products/products.parquet"

        result = derive(sample_products_df, dataset="products", batch_id=batch, source_file=src)

        row = result.select("_source_file", "_batch_id").first()
        assert row["_source_file"] == src
        assert row["_batch_id"] == batch

    def test_derive_order_date_from_timestamp(self, spark, sample_orders_df):
        """
        For the "orders" dataset, derive() must add order_date = date(order_timestamp).
        Input is source-shaped (has order_timestamp + date columns, no order_date).
        """
        result = derive(
            sample_orders_df,
            dataset="orders",
            batch_id="orders-20250401-abc",
            source_file="s3://bucket/orders_apr_2025.parquet",
        )

        assert "order_date" in result.columns, "derive() must produce order_date for orders dataset"

        # All timestamps are 2025-04-01, so order_date must be 2025-04-01
        expected_date = datetime.date(2025, 4, 1)
        dates = [row["order_date"] for row in result.select("order_date").collect()]
        assert all(
            d == expected_date for d in dates
        ), f"order_date mismatch; expected {expected_date}, got {set(dates)}"

    def test_derive_record_hash_non_null(self, spark, sample_products_df):
        """_record_hash must be non-null for every clean row."""
        result = derive(
            sample_products_df,
            dataset="products",
            batch_id="b1",
            source_file="f.csv",
        )
        null_hashes = result.filter(F.col("_record_hash").isNull()).count()
        assert null_hashes == 0, "_record_hash must never be null on clean rows"

    def test_derive_record_hash_changes_with_data(self, spark):
        """Two rows with different product_name must produce different _record_hash values."""
        schema = StructType(
            [
                StructField("product_id", IntegerType(), True),
                StructField("department_id", IntegerType(), True),
                StructField("department", StringType(), True),
                StructField("product_name", StringType(), True),
            ]
        )
        data = [
            (1, 1, "Books", "Book A"),
            (1, 1, "Books", "Book B"),
        ]
        df = spark.createDataFrame(data, schema=schema)
        result = derive(df, dataset="products", batch_id="b", source_file="f")
        hashes = [row["_record_hash"] for row in result.select("_record_hash").collect()]
        assert (
            hashes[0] != hashes[1]
        ), "Different business column values must produce different hashes"


# ---------------------------------------------------------------------------
# dedup
# ---------------------------------------------------------------------------


class TestDedup:
    def _make_dupes_df(self, spark):
        """
        Helper: two rows for the same order_id (1001) with different _ingest_ts.
        The row with the later _ingest_ts should survive.
        """
        schema = StructType(
            [
                StructField("order_num", IntegerType(), True),
                StructField("order_id", LongType(), True),
                StructField("user_id", LongType(), True),
                StructField("_ingest_ts", TimestampType(), True),
                StructField("_record_hash", StringType(), True),
                StructField("_source_file", StringType(), True),
                StructField("_batch_id", StringType(), True),
            ]
        )
        data = [
            (1, 1001, 501, datetime.datetime(2025, 4, 1, 10, 0, 0), "hash_old", "f.parquet", "b1"),
            (1, 1001, 501, datetime.datetime(2025, 4, 1, 11, 0, 0), "hash_new", "f.parquet", "b1"),
        ]
        return spark.createDataFrame(data, schema=schema)

    def test_dedup_keeps_latest(self, spark):
        """
        Two rows with same order_id but different _ingest_ts.
        Only the row with the later _ingest_ts must survive.
        dedup() takes the merge key column name (str), not dataset name.
        """
        df = self._make_dupes_df(spark)
        result = dedup(df, merge_key="order_id")

        assert result.count() == 1, "Dedup should collapse two rows with same key to one"
        surviving_hash = result.select("_record_hash").first()["_record_hash"]
        assert (
            surviving_hash == "hash_new"
        ), f"Expected hash_new (latest _ingest_ts) to survive, got {surviving_hash}"

    def test_dedup_deterministic_tiebreak(self, spark):
        """
        Two rows with same order_id AND same _ingest_ts — deterministic pick via
        _record_hash descending.  Result must be exactly 1 row (no ambiguity).
        """
        schema = StructType(
            [
                StructField("order_id", LongType(), True),
                StructField("user_id", LongType(), True),
                StructField("_ingest_ts", TimestampType(), True),
                StructField("_record_hash", StringType(), True),
                StructField("_source_file", StringType(), True),
                StructField("_batch_id", StringType(), True),
            ]
        )
        same_ts = datetime.datetime(2025, 4, 1, 10, 0, 0)
        data = [
            (1001, 501, same_ts, "hash_zzz", "f.parquet", "b1"),  # higher hash — wins
            (1001, 501, same_ts, "hash_aaa", "f.parquet", "b1"),
        ]
        df = spark.createDataFrame(data, schema=schema)

        result = dedup(df, merge_key="order_id")

        assert result.count() == 1, "Tiebreak must still collapse to exactly one row"
        surviving_hash = result.select("_record_hash").first()["_record_hash"]
        assert (
            surviving_hash == "hash_zzz"
        ), f"Higher _record_hash should win tiebreak, got {surviving_hash}"

    def test_dedup_preserves_unique_rows(self, spark, sample_orders_df):
        """
        If the input already has no duplicate keys, dedup must return the same
        number of rows unchanged.
        """
        # Attach stub audit columns so dedup can run
        df_with_audit = (
            sample_orders_df.withColumn("_ingest_ts", F.current_timestamp())
            .withColumn("_record_hash", F.sha2(F.col("order_id").cast("string"), 256))
            .withColumn("_source_file", F.lit("orders.parquet"))
            .withColumn("_batch_id", F.lit("b1"))
        )

        result = dedup(df_with_audit, merge_key="order_id")
        assert result.count() == sample_orders_df.count(), "No duplicates should mean no row loss"
