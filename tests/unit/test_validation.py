"""
Unit tests for src/lakehouse/validation.py and src/lakehouse/logging_utils.py

Actual API (matched to implemented signatures):

    from lakehouse.validation import apply_rules, PRODUCT_RULES, ORDER_RULES, RULES
        apply_rules(df, rules: List[Rule]) -> (valid_df, rejected_df)
        rejected_df has a "reject_reason" column (not "reason").

    from lakehouse.logging_utils import compute_metrics, gate
        compute_metrics(df_in, valid_df, rejected_df) -> dict
            Returns {"rows_in": n, "rows_valid": n, "rows_rejected": n, "reject_rate": float}
        gate(metrics, threshold=0.05) -> None
            Raises ValueError if metrics["reject_rate"] > threshold.

Dataset names in RULES dict use SHORT keys: "products", "orders", "order_items".
"""

import datetime
from decimal import Decimal

import pytest
from pyspark.sql import functions as F
from pyspark.sql.types import (
    DateType,
    DecimalType,
    IntegerType,
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

from lakehouse.logging_utils import compute_metrics, gate
from lakehouse.validation import (
    ORDER_ITEM_RULES,
    ORDER_RULES,
    PRODUCT_RULES,
    apply_rules,
)

# ---------------------------------------------------------------------------
# Schema helpers — post-enforce_types / post-derive shapes for validation
# ---------------------------------------------------------------------------

_PRODUCTS_SCHEMA = StructType(
    [
        StructField("product_id", IntegerType(), True),
        StructField("department_id", IntegerType(), True),
        StructField("department", StringType(), True),
        StructField("product_name", StringType(), True),
    ]
)

_ORDERS_SCHEMA = StructType(
    [
        StructField("order_num", IntegerType(), True),
        StructField("order_id", LongType(), True),
        StructField("user_id", LongType(), True),
        StructField("order_timestamp", TimestampType(), True),
        StructField("total_amount", DecimalType(10, 2), True),
        StructField("order_date", DateType(), True),
    ]
)

_ORDER_ITEMS_SCHEMA = StructType(
    [
        StructField("id", LongType(), True),
        StructField("order_id", LongType(), True),
        StructField("user_id", LongType(), True),
        StructField("days_since_prior_order", IntegerType(), True),
        StructField("product_id", IntegerType(), True),
        StructField("add_to_cart_order", IntegerType(), True),
        StructField("reordered", IntegerType(), True),
        StructField("order_timestamp", TimestampType(), True),
        StructField("order_date", DateType(), True),
    ]
)


def _make_products(spark, rows):
    return spark.createDataFrame(rows, schema=_PRODUCTS_SCHEMA)


def _make_orders(spark, rows):
    return spark.createDataFrame(rows, schema=_ORDERS_SCHEMA)


def _make_items(spark, rows):
    return spark.createDataFrame(rows, schema=_ORDER_ITEMS_SCHEMA)


# ---------------------------------------------------------------------------
# dim_products rules
# ---------------------------------------------------------------------------


class TestProductValidation:
    def test_valid_products_pass(self, spark, sample_products_df):
        """All 5 clean product rows → valid_df has 5 rows, rejected is empty."""
        valid_df, rejected_df = apply_rules(sample_products_df, PRODUCT_RULES)

        assert valid_df.count() == 5, f"Expected 5 valid rows, got {valid_df.count()}"
        assert rejected_df.count() == 0, f"Expected 0 rejected rows, got {rejected_df.count()}"

    def test_null_pk_quarantined(self, spark):
        """Rule P1: null product_id → quarantine; reject_reason mentions P1."""
        data = [(None, 1, "Books", "Some Product")]
        df = _make_products(spark, data)

        valid_df, rejected_df = apply_rules(df, PRODUCT_RULES)

        assert valid_df.count() == 0
        assert rejected_df.count() == 1

        reason = rejected_df.select("reject_reason").first()["reject_reason"].lower()
        assert (
            "p1" in reason or "null" in reason or "product_id" in reason
        ), f"reject_reason should mention P1/null/product_id, got: {reason}"

    def test_invalid_department_quarantined(self, spark):
        """Rule P4: department='Gadgets' is not in the allowed set → quarantine."""
        data = [(10, 2, "Gadgets", "Fancy Gadget")]
        df = _make_products(spark, data)

        valid_df, rejected_df = apply_rules(df, PRODUCT_RULES)

        assert valid_df.count() == 0
        assert rejected_df.count() == 1

        reason = rejected_df.select("reject_reason").first()["reject_reason"].lower()
        assert (
            "p4" in reason or "department" in reason or "allowed" in reason
        ), f"reject_reason should mention P4/department, got: {reason}"

    def test_empty_product_name_quarantined(self, spark):
        """Rule P5: empty product_name → quarantine."""
        data = [(11, 4, "Home", "")]
        df = _make_products(spark, data)

        valid_df, rejected_df = apply_rules(df, PRODUCT_RULES)

        assert rejected_df.count() == 1
        reason = rejected_df.select("reject_reason").first()["reject_reason"].lower()
        assert (
            "p5" in reason or "product_name" in reason or "blank" in reason
        ), f"reject_reason should mention P5/product_name, got: {reason}"

    def test_mixed_valid_and_invalid(self, spark):
        """Mixed batch: 2 valid + 2 invalid rows → correct split."""
        data = [
            (1, 1, "Books", "Good Book"),  # valid
            (2, 3, "Sports", "Good Ball"),  # valid
            (None, 1, "Books", "No PK"),  # P1 violation
            (3, 2, "Gadgets", "Bad Dept"),  # P4 violation
        ]
        df = _make_products(spark, data)

        valid_df, rejected_df = apply_rules(df, PRODUCT_RULES)

        assert valid_df.count() == 2
        assert rejected_df.count() == 2


# ---------------------------------------------------------------------------
# fct_orders rules
# ---------------------------------------------------------------------------


class TestOrderValidation:
    def test_valid_orders_pass(self, spark, sample_orders_df):
        """3 clean order rows → all valid, none rejected."""
        # sample_orders_df is source-shaped — add derived order_date for validation
        df_with_order_date = sample_orders_df.withColumn("order_date", F.to_date("order_timestamp"))
        valid_df, rejected_df = apply_rules(df_with_order_date, ORDER_RULES)

        assert valid_df.count() == 3
        assert rejected_df.count() == 0

    def test_future_timestamp_quarantined(self, spark):
        """Rule O5: order_timestamp in 2099 → quarantine."""
        data = [
            (
                1,
                1001,
                501,
                datetime.datetime(2099, 1, 1, 0, 0, 0),
                Decimal("50.00"),
                datetime.date(2099, 1, 1),
            )
        ]
        df = _make_orders(spark, data)

        valid_df, rejected_df = apply_rules(df, ORDER_RULES)

        assert rejected_df.count() == 1
        reason = rejected_df.select("reject_reason").first()["reject_reason"].lower()
        assert (
            "o5" in reason or "future" in reason or "timestamp" in reason
        ), f"reject_reason should mention O5/future/timestamp, got: {reason}"

    def test_negative_total_amount_quarantined(self, spark):
        """Rule O6: total_amount=-1 → quarantine."""
        data = [
            (
                1,
                1001,
                501,
                datetime.datetime(2025, 4, 1, 10, 0, 0),
                Decimal("-1.00"),
                datetime.date(2025, 4, 1),
            )
        ]
        df = _make_orders(spark, data)

        valid_df, rejected_df = apply_rules(df, ORDER_RULES)

        assert rejected_df.count() == 1
        reason = rejected_df.select("reject_reason").first()["reject_reason"].lower()
        assert (
            "o6" in reason or "total_amount" in reason or "negative" in reason
        ), f"reject_reason should mention O6/total_amount, got: {reason}"


# ---------------------------------------------------------------------------
# fct_order_items rules
# ---------------------------------------------------------------------------


class TestOrderItemsValidation:
    def test_reordered_out_of_set_quarantined(self, spark):
        """Rule I4: reordered=5 (not in {0,1}) → quarantine."""
        data = [
            (
                1,
                1001,
                501,
                7,
                1,
                1,
                5,
                datetime.datetime(2025, 4, 1),
                datetime.date(2025, 4, 1),
            )
        ]
        df = _make_items(spark, data)

        valid_df, rejected_df = apply_rules(df, ORDER_ITEM_RULES)

        assert rejected_df.count() == 1
        reason = rejected_df.select("reject_reason").first()["reject_reason"].lower()
        assert (
            "i4" in reason or "reordered" in reason
        ), f"reject_reason should mention I4/reordered, got: {reason}"


# ---------------------------------------------------------------------------
# Metrics + reject rate gate
# ---------------------------------------------------------------------------


class TestRejectRateGate:
    def test_reject_rate_gate_passes(self, spark, sample_products_df):
        """Clean data: reject_rate = 0% < 5% → no exception raised."""
        valid_df, rejected_df = apply_rules(sample_products_df, PRODUCT_RULES)
        # compute_metrics(df_in, df_valid, df_rejected) — df_in is the full pre-validation df
        metrics = compute_metrics(sample_products_df, valid_df, rejected_df)

        # Should not raise
        gate(metrics, threshold=0.05)

        assert metrics["reject_rate"] == 0.0

    def test_reject_rate_gate_fails(self, spark):
        """If reject_rate > 5%, gate() must raise ValueError."""
        # 1 valid row, 20 rejected rows → reject_rate ≈ 95.2%
        valid_data = [(1, 1, "Books", "Good Book")]
        valid_df = _make_products(spark, valid_data)

        # Build a minimal "rejected" DataFrame with reject_reason column
        rejected_data = [(i, 99, "Gadgets", f"Bad Row {i}") for i in range(2, 22)]
        rejected_df = _make_products(spark, rejected_data).withColumn(
            "reject_reason", F.lit("P4: department not in allowed set")
        )

        # df_in = union of valid + rejected to represent the full pre-validation batch
        df_in = valid_df.drop("reject_reason").union(rejected_df.drop("reject_reason"))
        metrics = compute_metrics(df_in, valid_df, rejected_df)

        assert metrics["reject_rate"] > 0.05, "Test setup: reject_rate must exceed 5%"

        with pytest.raises(ValueError, match=r"(?i)(reject|quality gate|failed)"):
            gate(metrics, threshold=0.05)

    def test_compute_metrics_fields(self, spark, sample_products_df):
        """compute_metrics must return rows_in, rows_valid, rows_rejected, reject_rate."""
        valid_df, rejected_df = apply_rules(sample_products_df, PRODUCT_RULES)
        metrics = compute_metrics(sample_products_df, valid_df, rejected_df)

        for key in ("rows_in", "rows_valid", "rows_rejected", "reject_rate"):
            assert key in metrics, f"metrics dict missing key '{key}'"

        assert metrics["rows_in"] == metrics["rows_valid"] + metrics["rows_rejected"]
