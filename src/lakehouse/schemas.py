"""
schemas.py — Canonical Delta Lake schemas for ecom-lakehouse.

This module is the in-code mirror of the Design Contract (docs/architecture.md §3.3).
Every other module (transforms, validation, normalize_to_parquet, validate_schema)
must import schemas from here — never hard-code column names or types elsewhere.

Two schema representations are provided for each dataset:
  1. Spark StructType  — enforced by Glue Spark jobs at read and on Delta write.
  2. EXPECTED_COLUMNS  — raw source column names used by the Lambda normalizer to
                         verify the file before any transformation.

Important: the raw source files (xlsx/csv) contain column names that differ from the
canonical Delta names:
  - orders: raw has "date", canonical uses "order_date" (derived from order_timestamp)
  - order_items: raw has "date", canonical uses "order_date"
  - products: raw column names match canonical names exactly.
EXPECTED_COLUMNS reflects the *raw* headers so the Lambda can gate bad files early.
"""

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
# Audit columns  (architecture.md §3.3 "Audit columns")
# These four columns are appended to every Delta table row by transforms.derive().
# They are NOT present in the raw source files or the Lambda staging Parquet.
# ---------------------------------------------------------------------------
AUDIT_FIELDS = [
    # Wall-clock timestamp when this row entered the pipeline (current_timestamp())
    StructField("_ingest_ts", TimestampType(), nullable=False),
    # S3 key of the source file that produced this row
    StructField("_source_file", StringType(), nullable=False),
    # Batch identifier: "{dataset}-{yyyymmdd}-{short_uuid}"  (architecture.md §3.7)
    StructField("_batch_id", StringType(), nullable=False),
    # SHA-256 hash of all business columns (concat_ws); used for MERGE no-op + dedup
    StructField("_record_hash", StringType(), nullable=False),
]

# ---------------------------------------------------------------------------
# dim_products  (architecture.md §3.3)
# Source: products.csv  (dimension; ~1 000 rows; clean sample)
# Merge key: product_id
# Z-ORDER: department
# ---------------------------------------------------------------------------
# nullable=False on PK and required business columns so that enforce_types() can
# detect a failed cast by testing for null on those fields.
DIM_PRODUCTS_SCHEMA = StructType(
    [
        StructField("product_id", IntegerType(), nullable=False),  # PK
        StructField("department_id", IntegerType(), nullable=False),
        StructField("department", StringType(), nullable=False),
        StructField("product_name", StringType(), nullable=False),
    ]
    + AUDIT_FIELDS
)

# ---------------------------------------------------------------------------
# fct_orders  (architecture.md §3.3)
# Source: orders_apr_2025.xlsx  (fact; ~500 rows; all 2025-04-01)
# Merge key: order_id
# Z-ORDER: order_date
# Note: order_date is DERIVED by transforms.derive() as date(order_timestamp).
#       The raw file has a "date" column which is dropped after derivation.
# ---------------------------------------------------------------------------
FCT_ORDERS_SCHEMA = StructType(
    [
        StructField("order_num", IntegerType(), nullable=False),
        StructField("order_id", LongType(), nullable=False),  # PK / merge key
        StructField("user_id", LongType(), nullable=False),
        StructField("order_timestamp", TimestampType(), nullable=False),
        StructField("total_amount", DecimalType(10, 2), nullable=False),
        StructField("order_date", DateType(), nullable=False),  # derived col; Z-ORDER key
    ]
    + AUDIT_FIELDS
)

# ---------------------------------------------------------------------------
# fct_order_items  (architecture.md §3.3)
# Source: order_items_apr_2025.xlsx  (fact; ~2 768 rows)
# Merge key: id
# Z-ORDER: order_date, product_id
# Note: order_date is DERIVED by transforms.derive() as date(order_timestamp).
#       The raw file has a "date" column which is dropped after derivation.
# ---------------------------------------------------------------------------
FCT_ORDER_ITEMS_SCHEMA = StructType(
    [
        StructField("id", LongType(), nullable=False),  # PK / merge key
        StructField("order_id", LongType(), nullable=False),  # FK → fct_orders
        StructField("user_id", LongType(), nullable=False),
        # nullable=True: days_since_prior_order is null on a customer's first order
        StructField("days_since_prior_order", IntegerType(), nullable=True),
        StructField("product_id", IntegerType(), nullable=False),  # FK → dim_products
        StructField("add_to_cart_order", IntegerType(), nullable=False),
        StructField("reordered", IntegerType(), nullable=False),
        StructField("order_timestamp", TimestampType(), nullable=False),
        StructField("order_date", DateType(), nullable=False),  # derived col; Z-ORDER key
    ]
    + AUDIT_FIELDS
)

# ---------------------------------------------------------------------------
# Schema registry by short dataset name
# Consumers should look up schemas via this dict so the key convention
# (short name, not table name) is used consistently everywhere.
# ---------------------------------------------------------------------------
SCHEMAS = {
    "products": DIM_PRODUCTS_SCHEMA,
    "orders": FCT_ORDERS_SCHEMA,
    "order_items": FCT_ORDER_ITEMS_SCHEMA,
}

# ---------------------------------------------------------------------------
# EXPECTED_COLUMNS — raw source headers validated by Lambda & validate_schema Lambda.
#
# These are the *actual* column names as they appear in the CSV/xlsx files.
# The Lambda normalizer checks that the downloaded file has exactly these columns
# before writing Parquet to staging.  order_date is NOT here because it is
# "date" in the raw files and is later renamed/derived by the Glue Spark job.
# ---------------------------------------------------------------------------
EXPECTED_COLUMNS = {
    "products": ["product_id", "department_id", "department", "product_name"],
    "orders": ["order_num", "order_id", "user_id", "order_timestamp", "total_amount", "date"],
    "order_items": [
        "id",
        "order_id",
        "user_id",
        "days_since_prior_order",
        "product_id",
        "add_to_cart_order",
        "reordered",
        "order_timestamp",
        "date",
    ],
}

# ---------------------------------------------------------------------------
# Valid department values  (architecture.md §3.3 — constraint on dim_products)
# Matches the 6 departments observed in products.csv and enforced by rule P4.
# ---------------------------------------------------------------------------
VALID_DEPARTMENTS = {"Books", "Sports", "Toys", "Home", "Clothing", "Electronics"}

# ---------------------------------------------------------------------------
# BUSINESS_COLS — columns included in the SHA-256 _record_hash computation.
# Audit columns (_ingest_ts, _source_file, _batch_id, _record_hash) are
# deliberately excluded — the hash must be deterministic across re-runs of the
# same source data, which audit cols would break.
# ---------------------------------------------------------------------------
BUSINESS_COLS = {
    "products": ["product_id", "department_id", "department", "product_name"],
    "orders": [
        "order_num",
        "order_id",
        "user_id",
        "order_timestamp",
        "total_amount",
        "order_date",
    ],
    "order_items": [
        "id",
        "order_id",
        "user_id",
        "days_since_prior_order",
        "product_id",
        "add_to_cart_order",
        "reordered",
        "order_timestamp",
        "order_date",
    ],
}
