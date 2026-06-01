# Data Handling — Ingestion, Normalization, Historical Backfill & Incremental

> Cites Design Contract (`architecture.md` §3). Owns Objectives **O1, O5, O8**.
> Covers how incoming **and historical** S3 data is integrated *seamlessly* into one
> code path. Resolves the `.xlsx` discrepancy (ADR-002).

## 1. Source reality

| dataset | provided file | format | rows | cadence signal |
|---------|---------------|--------|------|----------------|
| products | `products.csv` | CSV | 1,000 | reference/dimension (slowly changing) |
| orders | `orders_apr_2025.xlsx` | **XLSX** | 500 | monthly (`apr_2025`) |
| order_items | `order_items_apr_2025.xlsx` | **XLSX** | 2,768 | monthly (`apr_2025`) |

The brief says "ingested from CSVs"; reality is mixed CSV + XLSX. **Spark/Glue cannot
read `.xlsx` natively** → we normalize first (ADR-002).

## 2. Raw zone contract

- Drops land at `s3://ecom-lakehouse-raw-{env}/<dataset>/<yyyy>/<mm>/<filename>`.
- Raw is **immutable & versioned**; we never edit landed files.
- Accepted source formats: `.csv`, `.xlsx`. Anything else → rejected at normalization
  with a ledger `status=REJECTED_FORMAT` and an alert.
- A drop = one file per dataset per period. Filename pattern is validated:
  `^(products|orders|order_items)(_[a-z]{3}_\d{4})?\.(csv|xlsx)$`.

## 3. Step 0 — Normalization (xlsx/csv → Parquet)

An **AWS Lambda** function (pandas/openpyxl) — *not* Spark (ADR-011, revised — review 1.1;
fall back to a Glue Python-shell job only if a file exceeds Lambda limits):

1. Read the raw file with `pandas` (`openpyxl` engine for `.xlsx`, `read_csv` for CSV).
2. Assert the **expected column set** for the dataset against the **in-code schema**
   (`src/lakehouse/schemas.py`, which mirrors Design Contract §3.3 — this in-code schema is
   the single source of expected structure; the DynamoDB schema-registry table was removed,
   review 1.5).
3. Coerce column order/names to canonical; do **not** clean values here (cleaning is the
   Spark job's job) — only structural normalization + type-friendly Parquet write.
4. Write to `s3://ecom-lakehouse-staging-{env}/<dataset>/batch_id=<batch_id>/part.parquet`.
5. Record `batch_id`, source checksum (MD5/sha-256), and row count to the **ingestion
   ledger** (DynamoDB) with `status=NORMALIZED`.

Illustrative sketch (non-binding):

```python
# normalize_to_parquet.py  (AWS Lambda handler; EXPECTED imported from lakehouse.schemas)
import pandas as pd, hashlib, boto3
EXPECTED = {
  "products":    ["product_id","department_id","department","product_name"],
  "orders":      ["order_num","order_id","user_id","order_timestamp","total_amount","date"],
  "order_items": ["id","order_id","user_id","days_since_prior_order","product_id",
                  "add_to_cart_order","reordered","order_timestamp","date"],
}
def normalize(src_uri, dataset, batch_id, dst_uri):
    df = pd.read_excel(src_uri) if src_uri.endswith(".xlsx") else pd.read_csv(src_uri)
    missing = set(EXPECTED[dataset]) - set(df.columns)
    if missing:                      # structural schema drift → hard fail (ADR-008)
        raise ValueError(f"Schema drift for {dataset}: missing {missing}")
    df = df[EXPECTED[dataset]]        # canonical order; no value cleaning here
    df.to_parquet(dst_uri, index=False)
    return len(df)
```

**Why a separate step:** isolates the brittle Excel dependency in a cheap, testable
component; the Spark job becomes format-agnostic (always reads Parquet) and deterministic.

## 4. Two integration paths, one code path (O8)

The key principle: **historical backfill and ongoing incremental use the *same* Glue +
Delta MERGE pipeline.** Only the *driver* differs.

### 4.1 Incremental (ongoing, default)
- Trigger: new key under `raw/<dataset>/…` → S3 → EventBridge → Step Functions
  (or scheduled poll fallback; the brief allows "simulate trigger").
- Process exactly that one file's `batch_id`; MERGE into Delta; archive on success.
- Watermark in DynamoDB advances to the processed period.

### 4.2 Historical backfill ("incoming historical S3 bucket data, seamlessly")
- Trigger: a **backfill driver** (manual Step Functions execution / CLI) is given a
  prefix or list of historical keys (e.g. all of `raw/orders/2024/**`).
- The driver enumerates keys (oldest→newest to keep dimensions before facts and to make
  late corrections deterministic) and **fans them through the identical normalize → MERGE
  path**, one `batch_id` per file.
- **Idempotency makes this safe to re-run**: the ledger skips files already
  `status=ARCHIVED`; Delta MERGE prevents duplicate rows even if a backfill overlaps a
  prior incremental load (ADR-007).
- Throughput control: backfill runs with bounded concurrency (Step Functions `Map` with
  `MaxConcurrency`) so it doesn't overwhelm Glue capacity.

> Seamless = no special-case code. Backfill is "incremental, looped, with concurrency."

### 4.3 Load order & dependencies
`dim_products` should be current before facts load so referential-integrity checks have a
complete dimension. The orchestrator loads **products → orders → order_items** within a
batch (see `orchestration_stepfunctions.md`).

## 5. Archival (O5)

- **Only after a successful Delta MERGE + catalog update**, the orchestrator copies the
  original raw file to `s3://ecom-lakehouse-archive-{env}/<dataset>/<batch_id>/` and
  deletes (or tags) it from raw. Staging Parquet expires via lifecycle (7d).
- Archive class: **S3 Standard** (Intelligent-Tiering optional) — **not Glacier**
  (ADR-016, review 2.1): per-object overhead + transition fees exceed savings for ~1 MB
  files.
- Ledger row flips to `status=ARCHIVED` with archive URI — closes the idempotency loop.

## 6. Lifecycle & cost (revised — review 2.1 / ADR-016)

| zone | lifecycle |
|------|-----------|
| raw | S3 Standard; versioning on (Intelligent-Tiering optional — **no Glacier** for these small files) |
| staging | expire 7d (ephemeral Parquet) |
| dwh | indefinite; managed by Delta `VACUUM` (see `delta_lake_design.md`) |
| archive | S3 Standard, indefinite (Intelligent-Tiering optional — **no Glacier**) |
| quarantine | 180d then expire |
| athena-results | 30d |

> **Why no Glacier:** Glacier adds per-object metadata overhead, per-1,000-object
> transition request fees, and minimum-duration charges that outweigh storage savings for
> ~1 MB monthly files. Revisit only for objects > ~10 MB. Intelligent-Tiering is the safe
> default if access patterns/volume become unpredictable.

## 7. Failure handling at ingestion (cross-ref)
- Bad format / missing columns → fail fast, alert (`error_handling.md`).
- Partial bad rows → quarantine, continue (`data_validation.md`, ADR-008).
- Duplicate file re-drop → ledger short-circuits, no-op (idempotent).

## 8. Acceptance criteria
- A new raw drop is normalized to Parquet and recorded in the ledger.
- Re-dropping the same file does not create duplicate rows.
- A historical prefix can be backfilled through the same pipeline with bounded concurrency.
- Originals are archived only after a verified successful load.
