# Delta Lake Design — Tables, Partitioning, Optimization

> Cites Design Contract (`architecture.md` §3.3–3.5). Owns Objective **O3** + NFR
> (reliability, schema enforcement, freshness, US-5).

## 1. Tables in the DWH zone

All three tables are Delta, stored under `s3://ecom-lakehouse-dwh-{env}/<table>/`:

| table | type | path | partition (current) | Z-Order | merge key |
|-------|------|------|---------------------|---------|-----------|
| `dim_products` | dimension (SCD-1) | `…/dim_products/` | none | `department` | `product_id` |
| `fct_orders` | fact | `…/fct_orders/` | **none** | `order_date` | `order_id` |
| `fct_order_items` | fact | `…/fct_order_items/` | **none** | `order_date`, `product_id` | `id` |

## 2. Why Delta (recap of ADR-003)
ACID transactions, `MERGE`/upsert, **schema enforcement & evolution**, time travel,
`OPTIMIZE`/`Z-ORDER`, `VACUUM`. Glue 4.0+ has native Delta support.

## 3. Partitioning strategy (ADR-005, revised — review 1.2)

**Decision: unpartitioned + Z-Order now; promote to physical `order_date` partitioning at
a volume threshold.** This is a *justified* partitioning decision, not the absence of one
— directly addressing the brief's "Partitioning for performance" + "justify your
partitioning logic."

- **Why not physically partition by `order_date` today.** At ~500 orders / ~2,768 items
  per monthly drop, partitioning by date fragments the table into KB-scale Parquet files.
  Delta-log metadata growth + many tiny S3 GET requests make Athena/Spark **slower and
  costlier** — the classic small-file problem. (Note: a single monthly file carries one
  `order_date`, so date-partitioning would produce ~12 partitions/yr, not the "16 rows per
  partition" the review estimated — but the small-file conclusion holds.)
- **What we do instead.** Keep all three tables **unpartitioned**; rely on Delta
  **Z-ORDER + data skipping** for pruning: `ZORDER BY (order_date)` on `fct_orders`,
  `ZORDER BY (order_date, product_id)` on `fct_order_items`, `ZORDER BY (department)` on
  `dim_products`. Delta's per-file min/max statistics skip non-matching files on
  time-range queries — the pruning benefit without the fragmentation.
- **Promotion threshold (documented trigger).** Switch a fact table to physical
  `order_date` partitioning once a single date partition would hold a **meaningful** amount
  of data — rule of thumb **≥ ~1 GB/partition** (equivalently, sustained daily order
  volume in the high tens-of-thousands). Cutover = a one-time `OPTIMIZE`/rewrite + backfill;
  the merge keys and schema are unchanged.
- **Anti-pattern avoided:** never partition by high-cardinality keys (`order_id`,
  `user_id`) — millions of micro-partitions.

## 4. Schema enforcement & evolution
- **Enforcement is the default and desired behavior.** Writes that don't match the table
  schema fail — surfacing contract drift loudly (NFR). Validation (L1/L2) ensures data
  conforms *before* MERGE, so enforcement should never trip in normal operation.
- **No auto-merge in production.** `mergeSchema` is **off** in prod. Schema changes go
  through code review + a deliberate migration (documented), keeping the catalog stable
  for Athena consumers. Optional `dev` allowance only for exploration.

## 5. Small-file & layout management
Monthly micro-batches + MERGE rewrites create small/uneven files. Mitigations:
- Run `OPTIMIZE <table> ZORDER BY (<cols>)` after each successful load to compact
  (bin-pack) files and cluster the common filter columns (`order_date` on facts,
  `product_id` also on items, `department` on products). Tables are unpartitioned, so
  OPTIMIZE operates table-wide (cheap at this volume).
- Set `delta.targetFileSize` (~128 MB) and use `optimizeWrite`/`autoCompact` where Glue
  supports it.
- Coalesce job output partitions to a sensible count before write.

## 6. Retention, VACUUM & time travel
- `VACUUM <table> RETAIN 168 HOURS` (7 days) on a schedule to remove tombstoned files;
  retain ≥7d so time-travel/rollback and in-flight readers stay safe.
- Time travel (`VERSION AS OF`) is the **rollback mechanism** if a bad batch lands — we
  can restore the prior version (see `production_deployment.md`).
- Maintain `delta.logRetentionDuration` ≥ 30d for auditability.

## 7. Table properties (planned)
```
delta.appendOnly = false
delta.autoOptimize.optimizeWrite = true        # where supported
delta.autoOptimize.autoCompact = false         # explicit OPTIMIZE preferred
delta.logRetentionDuration = 'interval 30 days'
delta.deletedFileRetentionDuration = 'interval 7 days'
delta.minReaderVersion / minWriterVersion       # pinned for Athena compatibility
```

## 8. Athena/Glue compatibility note (revised — review 1.3)
Athena **v3 reads Delta natively** via the Glue Catalog (`table_type=DELTA`) directly from
the transaction log — **no symlink manifests, no `MSCK REPAIR`.** We pin Delta protocol
versions to those Athena v3 supports and register tables explicitly (ADR-015) — see
`catalog_and_athena.md`.

## 9. Concurrency & ACID
- MERGE is a single ACID transaction; concurrent writers to the same table are serialized
  by Delta's optimistic concurrency. Our orchestration loads one batch at a time per
  table, so write conflicts are rare; backfill `Map` concurrency is bounded and per-key
  MERGE targets rarely overlap.

## 10. Acceptance criteria
- Tables created unpartitioned with documented schema, Z-Order, and properties.
- `OPTIMIZE` reduces file count; `VACUUM` honors 7-day retention.
- Time-range queries skip files via Z-Order/data-skipping statistics.
- A bad batch can be rolled back via time travel.
- Athena queries the native Delta tables (no manifest/`MSCK` needed).
