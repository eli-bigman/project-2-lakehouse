-- =============================================================================
-- validation_queries.sql — Post-load validation queries for ecom-lakehouse
-- =============================================================================
-- These queries are run against the Athena workgroup ecom_lakehouse_wg_{env}
-- after each successful pipeline run to confirm:
--   1. Data presence and freshness
--   2. Referential integrity between fact and dimension tables
--   3. Deduplication correctness (no duplicate primary keys)
--
-- All queries target the Glue Data Catalog database: ecom_lakehouse_db_{env}
-- Tables are Delta-native (table_type=DELTA, ADR-015) — no symlink manifests,
-- no MSCK REPAIR needed.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- Query 1: Presence + freshness check on fct_orders
-- -----------------------------------------------------------------------------
-- Purpose: Verify that the fct_orders table contains rows and that the
--          most recently loaded order_date matches what was ingested.
-- Expected result:
--   n      — should be > 0 after the first load (≥ 500 after April 2025 drop)
--   latest — should equal the batch's order date (e.g. 2025-04-01 for apr batch)
-- If n = 0 → the MERGE wrote nothing (check ingest Glue job logs).
-- If latest is stale → the new batch may not have been processed yet.
-- -----------------------------------------------------------------------------
SELECT
    COUNT(*)            AS n,
    MAX(order_date)     AS latest
FROM fct_orders;


-- -----------------------------------------------------------------------------
-- Query 2: Referential integrity check — order_items → dim_products
-- -----------------------------------------------------------------------------
-- Purpose: Confirm there are zero orphan order_item rows that reference a
--          product_id not present in dim_products.
-- Expected result: COUNT(*) = 0
-- A non-zero count means the referential_integrity() check in the Glue job
-- either failed silently or dim_products was not loaded before order_items.
-- This query complements the in-pipeline RI check (validation.py) and acts
-- as an end-to-end verification after all three datasets have been loaded.
-- -----------------------------------------------------------------------------
SELECT
    COUNT(*) AS orphan_order_items
FROM
    fct_order_items oi
    LEFT JOIN dim_products dp
        ON oi.product_id = dp.product_id
WHERE
    dp.product_id IS NULL;


-- -----------------------------------------------------------------------------
-- Query 3: Deduplication sanity check on fct_orders
-- -----------------------------------------------------------------------------
-- Purpose: Confirm that there are zero duplicate order_id values in fct_orders
--          after the MERGE.  The pipeline's dedup() step and the MERGE upsert
--          together should guarantee uniqueness on the merge key.
-- Expected result: 0 rows returned (no order_id with count > 1)
-- If any rows are returned → investigate the dedup Window logic in
--   transforms.dedup() and the MERGE ON condition in merge.upsert().
-- -----------------------------------------------------------------------------
SELECT
    order_id,
    COUNT(*) AS c
FROM
    fct_orders
GROUP BY
    order_id
HAVING
    COUNT(*) > 1;
