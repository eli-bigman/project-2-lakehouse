# Glue Data Catalog & Amazon Athena

> Cites Design Contract (`architecture.md` §3.5) + `delta_lake_design.md`. Owns Objective
> **O4** + US-4/US-5. ADR-015 (explicit DDL preferred over crawler).

## 1. Catalog model
- Glue database: **`ecom_lakehouse_db_{env}`**.
- Tables: `dim_products`, `fct_orders`, `fct_order_items` (Delta), registered so Athena
  and Glue share one metadata layer.
- **Registration approach (ADR-015, revised — review 1.3):** register each table as a
  **native Delta table** (`'table_type' = 'DELTA'`) via explicit/managed DDL (Glue API
  `CreateTable` or Spark `saveAsTable` to the Glue catalog). Explicit DDL is deterministic,
  version-controlled, and avoids crawler type mis-inference (e.g. `decimal`→`double`).
- **Crawler stays optional** (brief allows it) for schema *discovery* in a `dev` sandbox.

## 2. Delta ↔ Athena compatibility (native, Athena v3)
**Athena v3 reads Delta natively** through the Glue Catalog: with `table_type=DELTA`,
Athena reads the **Delta transaction log directly** for file listing, partition pruning,
and schema. Therefore we **do not** generate `_symlink_format_manifest` files and **do
not** run `MSCK REPAIR` (these were older Athena v2 patterns). This removes per-load S3
manifest writes and catalog-repair cycles. We pin Delta reader/writer protocol versions
(`delta_lake_design.md` §7) to those Athena v3 supports.

## 3. Schema & partition awareness
- Tables are **unpartitioned** at current volume (ADR-005, revised): Athena prunes files
  via Delta's data-skipping statistics, so there are no partitions to register or repair.
- If/when a fact table is promoted to physical `order_date` partitioning (volume
  threshold, `delta_lake_design.md` §3), native Delta surfaces new partitions
  automatically from the transaction log — still no `MSCK`/manifest step required.

## 4. Athena workgroup & governance
- Workgroup **`ecom_lakehouse_wg_{env}`** with:
  - Query results → `ecom-lakehouse-athena-results-{env}` (enforced, encrypted).
  - **Per-query data-scanned limit** + workgroup cost guardrail.
  - CloudWatch metrics enabled (query count, bytes scanned).
- Engine version pinned (Athena v3) for Delta support + performance.

## 5. Validation queries (O4 + brief's optional Athena check)
Stored in `src/athena/validation_queries.sql`, run by the `AthenaValidate` step:
```sql
-- presence + freshness
SELECT COUNT(*) AS n, MAX(order_date) AS latest FROM fct_orders;
-- referential integrity (should be 0)
SELECT COUNT(*) FROM fct_order_items i
LEFT JOIN dim_products p USING (product_id) WHERE p.product_id IS NULL;
-- dedup sanity (should be 0)
SELECT order_id, COUNT(*) c FROM fct_orders GROUP BY order_id HAVING COUNT(*) > 1;
```
A non-zero RI/dedup result or a count below the ledger's `rows_valid` fails the run
(`orchestration_stepfunctions.md` `AthenaValidate` branch).

## 6. Consumer-facing layer (Gold, future)
- Curated Athena **views**/CTAS marts (e.g. `vw_daily_sales`, `vw_reorder_rate`) built on
  the Silver Delta tables — kept separate so the physical tables can evolve without
  breaking analysts. Out of scope for the core deliverable but designed-for.

## 7. Access & security
- Athena/Glue access via least-privilege IAM; optional **Lake Formation** for
  table/column grants if fine-grained governance is required (`security_iam.md`).

## 8. Acceptance criteria
- All three native-Delta tables queryable in Athena immediately after a successful run
  (no manifest generation, no `MSCK REPAIR`).
- Validation queries return expected counts; RI/dedup checks return 0.
- New data is visible to Athena directly from the Delta log after each load.
- Query results land encrypted in the results bucket under the workgroup.
