"""
Unit tests for src/lakehouse/merge.py

Actual API (matched to implemented signature):
    upsert(spark, df, target_path, merge_key: str) -> dict
        merge_key is the column name string: "product_id" | "order_id" | "id".
        Returns a stats dict (rows_inserted, rows_updated, etc.).

Merge semantics (from merge.py docstring):
  - First run:  write df as a new Delta table.
  - UPDATE when key matches AND _record_hash differs.
  - INSERT when key is new.
  - SKIP (no-op) when key matches AND _record_hash is identical.
"""

import datetime
from decimal import Decimal

import pytest
from delta.tables import DeltaTable
from pyspark.sql.types import (
    DecimalType,
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

from lakehouse.merge import upsert

# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

_ORDERS_DELTA_SCHEMA = StructType(
    [
        StructField("order_id", LongType(), False),
        StructField("user_id", LongType(), True),
        StructField("total_amount", DecimalType(10, 2), True),
        StructField("_ingest_ts", TimestampType(), True),
        StructField("_record_hash", StringType(), True),
        StructField("_source_file", StringType(), True),
        StructField("_batch_id", StringType(), True),
    ]
)


def _make_orders_df(spark, rows):
    return spark.createDataFrame(rows, schema=_ORDERS_DELTA_SCHEMA)


_TS_1 = datetime.datetime(2025, 4, 1, 10, 0, 0)
_TS_2 = datetime.datetime(2025, 4, 1, 12, 0, 0)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestUpsert:
    def test_upsert_creates_table_on_first_run(self, spark, tmp_delta_path):
        """
        Upserting to a path with no Delta table yet must create the table
        and load all rows from the source DataFrame.
        """
        data = [
            (1001, 501, Decimal("49.99"), _TS_1, "hash_a", "f.parquet", "b1"),
            (1002, 502, Decimal("129.50"), _TS_1, "hash_b", "f.parquet", "b1"),
        ]
        df = _make_orders_df(spark, data)

        upsert(spark, df, target_path=tmp_delta_path, merge_key="order_id")

        assert DeltaTable.isDeltaTable(spark, tmp_delta_path), "Target must be a Delta table"
        count = spark.read.format("delta").load(tmp_delta_path).count()
        assert count == 2, f"Expected 2 rows after first upsert, got {count}"

    def test_upsert_updates_changed_row(self, spark, tmp_delta_path):
        """
        Second upsert with an updated total_amount for an existing order_id must
        update the row in-place without creating a duplicate.
        """
        initial = [(1001, 501, Decimal("49.99"), _TS_1, "hash_old", "f.parquet", "b1")]
        df_initial = _make_orders_df(spark, initial)
        upsert(spark, df_initial, target_path=tmp_delta_path, merge_key="order_id")

        # New hash signals a real business change → update should fire
        updated = [(1001, 501, Decimal("99.99"), _TS_2, "hash_new", "f.parquet", "b2")]
        df_updated = _make_orders_df(spark, updated)
        upsert(spark, df_updated, target_path=tmp_delta_path, merge_key="order_id")

        result = spark.read.format("delta").load(tmp_delta_path)

        assert result.count() == 1, "Update must not create a duplicate row"

        new_amount = result.select("total_amount").first()["total_amount"]
        assert float(new_amount) == pytest.approx(99.99), (
            f"Expected updated total_amount=99.99, got {new_amount}"
        )

    def test_upsert_skips_identical_row(self, spark, tmp_delta_path):
        """
        Upserting the same row (same key AND same _record_hash) twice must be a
        no-op: row count stays 1 and the original hash value is preserved.
        """
        same_hash = "hash_stable_abc123"
        row = [(1001, 501, Decimal("49.99"), _TS_1, same_hash, "f.parquet", "b1")]
        df = _make_orders_df(spark, row)

        # First run — creates the table
        upsert(spark, df, target_path=tmp_delta_path, merge_key="order_id")

        # Second run — identical row, same hash → must be skipped
        upsert(spark, df, target_path=tmp_delta_path, merge_key="order_id")

        result = spark.read.format("delta").load(tmp_delta_path)
        assert result.count() == 1, "Identical row re-upsert must not add a duplicate"

        surviving_hash = result.select("_record_hash").first()["_record_hash"]
        assert surviving_hash == same_hash, "Hash must remain unchanged after no-op upsert"

    def test_upsert_inserts_new_row(self, spark, tmp_delta_path):
        """
        Upserting a new order_id that does not exist in the target must insert it,
        leaving the original rows intact.
        """
        initial = [(1001, 501, Decimal("49.99"), _TS_1, "hash_a", "f.parquet", "b1")]
        df_initial = _make_orders_df(spark, initial)
        upsert(spark, df_initial, target_path=tmp_delta_path, merge_key="order_id")

        new_row = [(1002, 502, Decimal("75.00"), _TS_2, "hash_b", "f.parquet", "b2")]
        df_new = _make_orders_df(spark, new_row)
        upsert(spark, df_new, target_path=tmp_delta_path, merge_key="order_id")

        result = spark.read.format("delta").load(tmp_delta_path)
        assert result.count() == 2, (
            f"Expected 2 rows after inserting new key, got {result.count()}"
        )

        order_ids = sorted([row["order_id"] for row in result.select("order_id").collect()])
        assert order_ids == [1001, 1002], f"Expected order_ids [1001, 1002], got {order_ids}"
