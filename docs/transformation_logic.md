# Transformation Logic — Cleaning, Dedup & Delta MERGE/Upsert

> Cites Design Contract (`architecture.md` §3.3–3.4). Owns Objective **O2** (transform)
> + **O3** (MERGE). Implements "modular, reusable Spark code" + "Merge/upsert logic" +
> "Deduplicated data."
>
> ⚠️ **All Delta I/O uses native Spark DataFrames** (`spark.read.format("delta")`,
> `DeltaTable.*`) — never Glue `DynamicFrame`. Glue's DynamicFrame API does not support
> Delta Lake and silently bypasses the transaction log. See ADR-019.

## 1. Transformation pipeline (per dataset, in the Spark job)

```
read Parquet (staging)                       [io.read_staging]
   → cast to canonical schema (Design Contract §3.3)   [transforms.enforce_types]
   → derive columns (order_date, _record_hash, audit)  [transforms.derive]
   → validate (L1–L5)                                  [validation.apply_rules]
   → split valid / quarantine                          [validation]
   → dedup within batch (keep latest per key)          [transforms.dedup]
   → MERGE into Delta target (upsert)                  [merge.upsert]
   → write quarantine + emit metrics + update ledger
```

Each arrow is a pure, unit-testable function in `src/lakehouse/`. The Glue entrypoint is
thin: parse args → orchestrate these calls.

## 2. Type enforcement & derivations

- Cast every column to its Contract dtype (e.g. `total_amount` → `DecimalType(10,2)`,
  `order_timestamp` → `TimestampType`). Uncastable → null → caught by L1 → quarantine.
- Derive `order_date = to_date(order_timestamp)` (authoritative) and cross-check against
  the source `date` column (rule O7) — guards against inconsistent source dates.
- Stamp audit columns: `_ingest_ts = current_timestamp()`, `_source_file`, `_batch_id`,
  and `_record_hash = sha2(concat_ws('||', <business cols>), 256)` (ADR-014).

Sketch (non-binding):

```python
def derive(df, dataset, batch_id, source_file):
    biz = BUSINESS_COLS[dataset]
    return (df
        .withColumn("order_date", F.to_date("order_timestamp"))
        .withColumn("_ingest_ts", F.current_timestamp())
        .withColumn("_source_file", F.lit(source_file))
        .withColumn("_batch_id", F.lit(batch_id))
        .withColumn("_record_hash", F.sha2(F.concat_ws("||", *biz), 256)))
```

## 3. Deduplication (brief: "Deduplicated data", "Deduplication across files")

Two scopes:

1. **Within-batch dedup** — a file may contain duplicate keys. Keep one row per merge key,
   choosing the latest by `_ingest_ts` then highest `_record_hash` (deterministic tie-break):

   ```python
   w = Window.partitionBy(MERGE_KEY[dataset]).orderBy(F.col("_ingest_ts").desc(),
                                                       F.col("_record_hash").desc())
   deduped = df.withColumn("_rn", F.row_number().over(w)).filter("_rn = 1").drop("_rn")
   ```

2. **Across-file / against-existing dedup** — handled by the **Delta MERGE on the natural
   key**: re-processing an overlapping file updates the existing row instead of inserting
   a duplicate. `_record_hash` lets us skip no-op updates (don't rewrite identical rows).

This satisfies "deduplication across files" precisely: dedup is keyed, not row-identity.

## 4. MERGE / upsert logic (O3)

Per table merge key (Contract §3.4): `dim_products`→`product_id`,
`fct_orders`→`order_id`, `fct_order_items`→`id`.

```python
# merge.py  (non-binding sketch)
def upsert(spark, df, target_path, key):
    if not DeltaTable.isDeltaTable(spark, target_path):
        (df.write.format("delta")
            .save(target_path))   # unpartitioned (ADR-005); OPTIMIZE ZORDER runs post-load
        return
    tgt = DeltaTable.forPath(spark, target_path)
    (tgt.alias("t").merge(df.alias("s"), f"t.{key} = s.{key}")
        .whenMatchedUpdateAll(condition="s._record_hash <> t._record_hash")  # skip no-ops
        .whenNotMatchedInsertAll()
        .execute())
```

Semantics:
- **Insert** new keys; **update** changed rows (hash differs); **skip** identical rows.
- `dim_products` behaves as an **upsert SCD-Type-1** (overwrite latest attributes). If
  history is ever required, switch to SCD-2 with effective dates (noted as future work).
- File skipping: tables are unpartitioned (ADR-005), so the MERGE relies on Delta
  **data-skipping** (min/max stats, reinforced by `OPTIMIZE ZORDER BY (order_date,…)`) to
  limit scanned files; adding `t.order_date = s.order_date` to the merge predicate further
  narrows the candidate files for a single-date batch.

## 5. Ordering & referential integrity within a run
Load **products → orders → order_items** so FK checks (rules I7/I8) see complete parents.
If a backfill loads facts before a dimension row exists, the orphan is quarantined and
re-driven after the dimension catches up (idempotent re-run picks it up).

## 6. Modularity & reuse (brief requirement)
- One generic `run_ingest(dataset)` driver parameterized by `dataset`; the only
  per-dataset differences are: schema, rule set, merge key, Z-Order columns — all data, not
  code. Adding a 4th dataset = add a config entry + rule list, no new pipeline.
- Shared helpers (`io`, `transforms`, `validation`, `merge`, `ledger`) are imported by
  every job and covered by unit tests (`testing_strategy.md`).

## 7. Performance considerations
- Broadcast `dim_products` (~1k rows) for FK joins.
- Coalesce output to avoid small files; rely on Delta `OPTIMIZE` post-write
  (`delta_lake_design.md`).
- Use predicate pushdown on Parquet staging reads.

## 8. Acceptance criteria
- Re-running the same batch produces zero net change (idempotent MERGE).
- Duplicate keys within a file collapse to the latest deterministically.
- Changed attributes update in place; unchanged rows are not rewritten.
- Orphan FK rows are quarantined, not inserted.
