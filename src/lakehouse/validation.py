"""
validation.py — Business-rule validation engine for ecom-lakehouse.

Design philosophy (ADR-008): validation is fail-soft — rows that violate
business rules are quarantined with a structured reject reason rather than
aborting the entire batch.  The batch is only aborted if the *fraction* of
rejected rows exceeds REJECT_THRESHOLD (5%).  This allows partial loads of
clean data while preventing wholesale ingestion of corrupted files.

All rules are expressed as filter predicates on a Spark DataFrame so that
they execute as a single distributed scan — no row-by-row Python loops.

Rule structure:
    name      — short code (P1, O1, I1 …) for ledger/log references
    column    — primary column the rule validates (for documentation; may
                be "multiple" for cross-column rules)
    predicate — a PySpark Column expression that evaluates to True for VALID rows
    reason    — human-readable string written to the reject_reason column
    severity  — "ERROR" (quarantine) | "WARN" (log only, future use)
"""

from collections import namedtuple
from functools import reduce as py_reduce
from typing import List, Tuple

import pyspark.sql.functions as F
from pyspark.sql import DataFrame, SparkSession

from lakehouse.schemas import VALID_DEPARTMENTS
from lakehouse.config import TABLE_PATH, DATASET_TO_TABLE
from lakehouse import io as lake_io

# ---------------------------------------------------------------------------
# Rule namedtuple
# ---------------------------------------------------------------------------
Rule = namedtuple("Rule", ["name", "column", "predicate", "reason", "severity"])

# ---------------------------------------------------------------------------
# dim_products rules  (P-series)
# Reference: docs/data_validation.md and architecture.md §3.3
# ---------------------------------------------------------------------------
PRODUCT_RULES: List[Rule] = [
    # P1: Primary key must be present and positive
    #     A null or zero/negative product_id would make MERGE ambiguous.
    Rule(
        name="P1",
        column="product_id",
        predicate=F.col("product_id").isNotNull() & (F.col("product_id") > 0),
        reason="P1: product_id is null or non-positive",
        severity="ERROR",
    ),
    # P2: Uniqueness is handled by dedup() before this stage;
    #     no runtime rule needed (documented as dedup responsibility).
    # P3: department_id must not be null
    Rule(
        name="P3",
        column="department_id",
        predicate=F.col("department_id").isNotNull(),
        reason="P3: department_id is null",
        severity="ERROR",
    ),
    # P4: department value must be in the controlled vocabulary
    #     VALID_DEPARTMENTS = {Books, Sports, Toys, Home, Clothing, Electronics}
    Rule(
        name="P4",
        column="department",
        predicate=F.col("department").isin(list(VALID_DEPARTMENTS)),
        reason="P4: department not in allowed set (Books/Sports/Toys/Home/Clothing/Electronics)",
        severity="ERROR",
    ),
    # P5: product_name must be present and non-empty (strip whitespace)
    Rule(
        name="P5",
        column="product_name",
        predicate=F.col("product_name").isNotNull()
        & (F.trim(F.col("product_name")) != ""),
        reason="P5: product_name is null or blank",
        severity="ERROR",
    ),
]

# ---------------------------------------------------------------------------
# fct_orders rules  (O-series)
# Reference: docs/data_validation.md
# ---------------------------------------------------------------------------
ORDER_RULES: List[Rule] = [
    # O1: order_id is the merge key — must be present and positive
    Rule(
        name="O1",
        column="order_id",
        predicate=F.col("order_id").isNotNull() & (F.col("order_id") > 0),
        reason="O1: order_id is null or non-positive",
        severity="ERROR",
    ),
    # O3: order_num must be present (sequence number, used for ordering within a user)
    Rule(
        name="O3",
        column="order_num",
        predicate=F.col("order_num").isNotNull(),
        reason="O3: order_num is null",
        severity="ERROR",
    ),
    # O4: user_id must be present (no anonymous orders)
    Rule(
        name="O4",
        column="user_id",
        predicate=F.col("user_id").isNotNull(),
        reason="O4: user_id is null",
        severity="ERROR",
    ),
    # O5: order_timestamp must be valid (not null, not in the future, >= 2020-01-01)
    #     Future timestamps typically indicate a system clock error or bad data.
    #     The 2020-01-01 floor pre-dates the business but guards against epoch=0 or
    #     1970-01-01 artefacts from bad type coercion.
    Rule(
        name="O5",
        column="order_timestamp",
        predicate=(
            F.col("order_timestamp").isNotNull()
            & (F.col("order_timestamp") <= F.current_timestamp())
            & (F.col("order_timestamp") >= F.lit("2020-01-01").cast("timestamp"))
        ),
        reason="O5: order_timestamp is null, in the future, or before 2020-01-01",
        severity="ERROR",
    ),
    # O6: total_amount must be non-negative and within a sanity ceiling (1,000,000)
    #     Negative amounts indicate refunds/credits which are not in scope for this model.
    #     The ceiling of 1e6 guards against data entry errors (e.g., amount in cents).
    Rule(
        name="O6",
        column="total_amount",
        predicate=(
            F.col("total_amount").isNotNull()
            & (F.col("total_amount") >= 0)
            & (F.col("total_amount") <= 1_000_000)
        ),
        reason="O6: total_amount is null, negative, or exceeds 1,000,000",
        severity="ERROR",
    ),
    # O7: order_date must equal date(order_timestamp) — cross-column consistency check
    #     The raw xlsx has a "date" column; after transforms.derive() drops it and
    #     re-derives order_date, this rule confirms the derivation is consistent
    #     (guards against an upstream system sending mismatched date fields).
    Rule(
        name="O7",
        column="order_date",
        predicate=(
            F.col("order_date").isNotNull()
            & (F.col("order_date") == F.to_date(F.col("order_timestamp")))
        ),
        reason="O7: order_date does not match date(order_timestamp)",
        severity="ERROR",
    ),
]

# ---------------------------------------------------------------------------
# fct_order_items rules  (I-series)
# Reference: docs/data_validation.md
# ---------------------------------------------------------------------------
ORDER_ITEM_RULES: List[Rule] = [
    # I1: id is the merge key — must be present and positive
    Rule(
        name="I1",
        column="id",
        predicate=F.col("id").isNotNull() & (F.col("id") > 0),
        reason="I1: id is null or non-positive",
        severity="ERROR",
    ),
    # I2: order_id foreign key must be present
    #     Referential integrity against fct_orders is checked separately in
    #     referential_integrity() — this rule only checks non-null.
    Rule(
        name="I2",
        column="order_id",
        predicate=F.col("order_id").isNotNull(),
        reason="I2: order_id is null",
        severity="ERROR",
    ),
    # I3: product_id foreign key must be present
    #     Referential integrity against dim_products checked in referential_integrity().
    Rule(
        name="I3",
        column="product_id",
        predicate=F.col("product_id").isNotNull(),
        reason="I3: product_id is null",
        severity="ERROR",
    ),
    # I4: reordered is a binary flag — must be 0 or 1
    Rule(
        name="I4",
        column="reordered",
        predicate=F.col("reordered").isin(0, 1),
        reason="I4: reordered must be 0 or 1",
        severity="ERROR",
    ),
    # I5: add_to_cart_order is 1-indexed — a zero or negative value is invalid
    Rule(
        name="I5",
        column="add_to_cart_order",
        predicate=F.col("add_to_cart_order").isNotNull()
        & (F.col("add_to_cart_order") >= 1),
        reason="I5: add_to_cart_order must be >= 1",
        severity="ERROR",
    ),
    # I6: days_since_prior_order is allowed to be null (first order for a user)
    #     When present it must be in [0, 365] — values outside that window are
    #     data quality issues (e.g., system clock drift, data entry error).
    Rule(
        name="I6",
        column="days_since_prior_order",
        predicate=(
            F.col("days_since_prior_order").isNull()
            | (
                (F.col("days_since_prior_order") >= 0)
                & (F.col("days_since_prior_order") <= 365)
            )
        ),
        reason="I6: days_since_prior_order is out of range [0, 365]",
        severity="ERROR",
    ),
]

# ---------------------------------------------------------------------------
# RULES registry — maps short dataset name → rule list
# ---------------------------------------------------------------------------
RULES = {
    "products": PRODUCT_RULES,
    "orders": ORDER_RULES,
    "order_items": ORDER_ITEM_RULES,
}


def apply_rules(
    df: DataFrame, rules: List[Rule]
) -> Tuple[DataFrame, DataFrame]:
    """
    Apply a list of Rule objects to a DataFrame.

    Strategy:
      For each rule, evaluate the predicate as a boolean column.
      A row is VALID only if ALL rules pass.  A row that fails ANY rule is
      rejected.  For rejected rows we collect the names of all failed rules
      and concatenate them into a "reject_reason" column (comma-separated).

    This approach requires a single pass over the rules to add per-rule
    flag columns, followed by a filter split — two scans total, which is
    more efficient than iterative DataFrame subtractions.

    Args:
        df:    Input DataFrame after enforce_types() and derive().
        rules: List of Rule namedtuples for the current dataset.

    Returns:
        Tuple of (valid_df, rejected_df) where:
          - valid_df   has the same columns as df (rule flag cols dropped)
          - rejected_df has the same columns as df plus "reject_reason" (string)
    """
    flag_cols = []

    for rule in rules:
        flag_col = f"_rule_{rule.name}"
        flag_cols.append(flag_col)
        # True if the row PASSES the rule; False if it fails
        df = df.withColumn(flag_col, rule.predicate)

    # A row is valid if every rule flag is True.
    # Use functools.reduce (py_reduce) — NOT pyspark.sql.functions.reduce,
    # which is an array higher-order function with a completely different signature.
    all_pass_expr = py_reduce(lambda a, b: a & b, [F.col(c) for c in flag_cols])

    # Build the reject_reason string: concatenate reason strings for all failed rules.
    # F.when returns null when the condition is False — concat_ws skips nulls
    # automatically, so stray "; " separators are avoided without coalesce.
    reason_exprs = [
        F.when(~F.col(f"_rule_{rule.name}"), F.lit(rule.reason))
        for rule in rules
    ]
    reject_reason_expr = F.concat_ws("; ", *reason_exprs)

    df = df.withColumn("_all_pass", all_pass_expr)
    df = df.withColumn("reject_reason", reject_reason_expr)

    valid_df = (
        df.filter(F.col("_all_pass"))
        .drop(*flag_cols, "_all_pass", "reject_reason")
    )
    rejected_df = (
        df.filter(~F.col("_all_pass"))
        .drop(*flag_cols, "_all_pass")
    )

    return valid_df, rejected_df


def referential_integrity(
    spark: SparkSession,
    df: DataFrame,
    dataset: str,
    dwh_bucket: str,
    env: str,
) -> Tuple[DataFrame, DataFrame]:
    """
    Foreign-key referential integrity checks for order_items (fact table only).

    Checks performed:
      1. product_id must exist in dim_products (already loaded in this or a prior run).
      2. order_id must exist in fct_orders (already loaded in this or a prior run).

    Design decision: if the target Delta table does not yet exist (first-ever run,
    or dim_products hasn't been loaded yet) we skip that FK check and log a warning
    rather than crashing — this allows bootstrapping the pipeline in the correct
    dataset order (DATASET_LOAD_ORDER = products → orders → order_items).

    Uses left-anti join: rows in df that have NO matching key in the reference table
    become "orphans" and are written to quarantine.

    This function is a no-op for "products" and "orders" datasets.

    Args:
        spark:          Active SparkSession.
        df:             Valid DataFrame after apply_rules().
        dataset:        Short dataset name.
        dwh_bucket:     Name of the DWH S3 bucket.
        env:            Environment suffix.

    Returns:
        Tuple of (ri_valid_df, orphans_df) where orphans_df has a "reject_reason" col.
    """
    if dataset != "order_items":
        # RI checks only apply to the order_items fact table
        return df, spark.createDataFrame([], df.schema)

    orphan_dfs = []

    # -- Check 1: product_id → dim_products --------------------------------
    products_path = f"s3://{dwh_bucket}/{DATASET_TO_TABLE['products']}/"
    if lake_io.delta_table_exists(spark, products_path):
        dim_products = spark.read.format("delta").load(products_path).select("product_id")
        product_orphans = (
            df.join(dim_products, on="product_id", how="left_anti")
            .withColumn(
                "reject_reason",
                F.lit("RI: product_id not found in dim_products"),
            )
        )
        orphan_dfs.append(product_orphans)
        # Keep only rows whose product_id matched
        df = df.join(dim_products, on="product_id", how="inner")
    else:
        # dim_products not yet loaded — skip RI check for this run
        import logging
        logging.getLogger(__name__).warning(
            "referential_integrity: dim_products Delta table not found at %s; "
            "skipping product_id FK check",
            products_path,
        )

    # -- Check 2: order_id → fct_orders ------------------------------------
    orders_path = f"s3://{dwh_bucket}/{DATASET_TO_TABLE['orders']}/"
    if lake_io.delta_table_exists(spark, orders_path):
        fct_orders = spark.read.format("delta").load(orders_path).select("order_id")
        order_orphans = (
            df.join(fct_orders, on="order_id", how="left_anti")
            .withColumn(
                "reject_reason",
                F.lit("RI: order_id not found in fct_orders"),
            )
        )
        orphan_dfs.append(order_orphans)
        # Keep only rows whose order_id matched
        df = df.join(fct_orders, on="order_id", how="inner")
    else:
        import logging
        logging.getLogger(__name__).warning(
            "referential_integrity: fct_orders Delta table not found at %s; "
            "skipping order_id FK check",
            orders_path,
        )

    # Combine all orphan DataFrames
    if orphan_dfs:
        from functools import reduce

        orphans_df = reduce(lambda a, b: a.unionByName(b, allowMissingColumns=True), orphan_dfs)
    else:
        orphans_df = spark.createDataFrame([], df.schema)

    return df, orphans_df
