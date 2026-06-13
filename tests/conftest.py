"""
Pytest configuration and shared fixtures for the ecom-lakehouse test suite.

Fixtures are designed to be used at the *right pipeline stage*:
  - sample_*_df   → source-shaped rows, before derive() or enforce_types()
  - dirty_*_df    → rows with known-bad data for validation rule tests
  - tmp_delta_path → temp directory string for local Delta writes
"""

import datetime
import os
from decimal import Decimal

import pytest
from pyspark.sql import SparkSession
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

# ---------------------------------------------------------------------------
# Ensure the Delta JAR is available for local Spark sessions.
# delta-spark provides a helper that injects the required JARs via PySpark's
# --packages mechanism.  We set PYSPARK_SUBMIT_ARGS here so the session
# builder picks it up automatically when running under pytest.
# ---------------------------------------------------------------------------
os.environ.setdefault(
    "PYSPARK_SUBMIT_ARGS",
    "--packages io.delta:delta-spark_2.12:3.2.0 pyspark-shell",
)


# ---------------------------------------------------------------------------
# SparkSession (session-scoped — created once, shared across all tests)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def spark(tmp_path_factory):
    """
    Local SparkSession with Delta Lake extensions enabled.

    Uses delta-spark's configure_spark_with_delta_pip to inject the Delta
    JAR via the --packages flag so no manual JAR download is needed.
    """
    from delta import configure_spark_with_delta_pip

    warehouse = str(tmp_path_factory.mktemp("warehouse"))

    builder = (
        SparkSession.builder.master("local[1]")
        .appName("ecom-lakehouse-tests")
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config(
            "spark.sql.catalog.spark_catalog",
            "org.apache.spark.sql.delta.catalog.DeltaCatalog",
        )
        .config("spark.sql.warehouse.dir", warehouse)
        # Disable noisy UI for tests
        .config("spark.ui.enabled", "false")
        # Speed up small-data tests
        .config("spark.sql.shuffle.partitions", "1")
        .config("spark.default.parallelism", "1")
    )

    session = configure_spark_with_delta_pip(builder).getOrCreate()
    session.sparkContext.setLogLevel("ERROR")

    yield session

    session.stop()


# ---------------------------------------------------------------------------
# dim_products fixtures
# ---------------------------------------------------------------------------

# Canonical source schema for products (before enforce_types / derive)
_PRODUCTS_SOURCE_SCHEMA = StructType(
    [
        StructField("product_id", IntegerType(), True),
        StructField("department_id", IntegerType(), True),
        StructField("department", StringType(), True),
        StructField("product_name", StringType(), True),
    ]
)


@pytest.fixture(scope="session")
def sample_products_df(spark):
    """
    5 clean product rows matching the canonical dim_products schema.
    Valid for all rules P1–P5.
    """
    data = [
        (1, 1, "Books", "The Great Gatsby"),
        (2, 2, "Electronics", "Wireless Headphones"),
        (3, 3, "Sports", "Tennis Racket"),
        (4, 4, "Home", "Coffee Maker"),
        (5, 5, "Clothing", "Running Shoes"),
    ]
    return spark.createDataFrame(data, schema=_PRODUCTS_SOURCE_SCHEMA)


@pytest.fixture(scope="session")
def dirty_products_df(spark):
    """
    Product rows with known-bad data covering rules P1, P4, P5.

    Row 1: null product_id              (violates P1)
    Row 2: department='Gadgets'         (violates P4 — not in allowed set)
    Row 3: empty product_name           (violates P5)
    Row 4: fully valid control row
    """
    data = [
        (None, 1, "Books", "Missing PK"),  # P1 violation
        (6, 2, "Gadgets", "Invalid Dept Product"),  # P4 violation
        (7, 4, "Home", ""),  # P5 violation — empty name
        (8, 3, "Sports", "Valid Row"),  # clean
    ]
    return spark.createDataFrame(data, schema=_PRODUCTS_SOURCE_SCHEMA)


# ---------------------------------------------------------------------------
# fct_orders fixtures
# Source-shaped: has order_timestamp + date columns; order_date is DERIVED
# ---------------------------------------------------------------------------

_ORDERS_SOURCE_SCHEMA = StructType(
    [
        StructField("order_num", IntegerType(), True),
        StructField("order_id", LongType(), True),
        StructField("user_id", LongType(), True),
        StructField("order_timestamp", TimestampType(), True),
        StructField("total_amount", DecimalType(10, 2), True),
        StructField("date", DateType(), True),
    ]
)


@pytest.fixture(scope="session")
def sample_orders_df(spark):
    """
    3 clean order rows.  Source-shaped: includes `date` column but NOT
    `order_date` (that is derived by transforms.derive).
    """
    d = datetime.datetime
    dd = datetime.date
    data = [
        (1, 1001, 501, d(2025, 4, 1, 10, 0, 0), Decimal("49.99"), dd(2025, 4, 1)),
        (2, 1002, 502, d(2025, 4, 1, 11, 30, 0), Decimal("129.50"), dd(2025, 4, 1)),
        (3, 1003, 503, d(2025, 4, 1, 14, 0, 0), Decimal("75.00"), dd(2025, 4, 1)),
    ]
    return spark.createDataFrame(data, schema=_ORDERS_SOURCE_SCHEMA)


# ---------------------------------------------------------------------------
# fct_order_items fixtures
# ---------------------------------------------------------------------------

_ORDER_ITEMS_SOURCE_SCHEMA = StructType(
    [
        StructField("id", LongType(), True),
        StructField("order_id", LongType(), True),
        StructField("user_id", LongType(), True),
        StructField("days_since_prior_order", IntegerType(), True),
        StructField("product_id", IntegerType(), True),
        StructField("add_to_cart_order", IntegerType(), True),
        StructField("reordered", IntegerType(), True),
        StructField("order_timestamp", TimestampType(), True),
        StructField("date", DateType(), True),
    ]
)


@pytest.fixture(scope="session")
def sample_order_items_df(spark):
    """
    5 clean order_item rows.  FK-valid against sample_orders (1001–1003)
    and sample_products (1–5).  Source-shaped: includes `date` but not `order_date`.
    """
    d = datetime.datetime
    dd = datetime.date
    data = [
        (1, 1001, 501, 7, 1, 1, 0, d(2025, 4, 1, 10, 0, 0), dd(2025, 4, 1)),
        (2, 1001, 501, 7, 2, 2, 1, d(2025, 4, 1, 10, 0, 0), dd(2025, 4, 1)),
        (3, 1002, 502, 14, 3, 1, 0, d(2025, 4, 1, 11, 30, 0), dd(2025, 4, 1)),
        (4, 1002, 502, 14, 4, 2, 0, d(2025, 4, 1, 11, 30, 0), dd(2025, 4, 1)),
        (5, 1003, 503, 21, 5, 1, 1, d(2025, 4, 1, 14, 0, 0), dd(2025, 4, 1)),
    ]
    return spark.createDataFrame(data, schema=_ORDER_ITEMS_SOURCE_SCHEMA)


# ---------------------------------------------------------------------------
# Temp Delta path helper
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_delta_path(tmp_path):
    """Returns a string path under tmp_path for use as a local Delta table location."""
    return str(tmp_path / "delta_table")
