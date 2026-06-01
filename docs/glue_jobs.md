# AWS Glue Jobs — Design & Modular Spark

> Cites Design Contract + `transformation_logic.md`, `data_validation.md`. Owns the
> compute layer for **O2/O3**. Implements ADR-011 (compute split) and "modular,
> reusable Spark code."

## 1. Job inventory

| job | type | trigger | purpose |
|-----|------|---------|---------|
| `normalize_to_parquet` | **AWS Lambda** (pandas/openpyxl) | per file, from SF | xlsx/csv → Parquet (ADR-002, ADR-011 revised) |
| `ingest_<dataset>` | Glue **Spark** (Delta) | **per dataset**, from SF | validate → dedup → MERGE |
| `optimize_<dataset>` | Glue **Spark** | post-load / scheduled | `OPTIMIZE`/`VACUUM` |

In practice the three `ingest_*` jobs are **one parameterized Spark job** invoked with
`--dataset products|orders|order_items` (modularity — see `transformation_logic.md` §6).
We keep thin per-dataset wrapper scripts only if Glue ergonomics require distinct job
names; logic lives in the shared `lakehouse` library.

> **Per-dataset is the default (review 1.4a).** The brief requires *"Run a Glue Job (with
> Delta Lake) for each dataset,"* and per-dataset runs give clean per-dataset retry/
> branching in Step Functions. **Optional optimization:** the same job can run all
> datasets present for a batch in **one Spark session** (`--dataset all`, dependency order
> products→orders→items) to avoid paying cluster-startup overhead three times — use this
> only for scheduled full-batch runs where the single-session cost saving outweighs losing
> per-dataset retry granularity. We **do not** adopt the review's Rust/`deltalake`-in-Lambda
> alternative: the brief mandates Glue + Spark (ADR-011, review 1.4b rejected).

## 2. Glue job configuration (planned)

| setting | value | rationale |
|---------|-------|-----------|
| Glue version | 4.0+ | native Delta, Spark 3.3+ |
| Worker type | `G.1X` | small data; scale to `G.2X` only if profiling shows bottleneck |
| Number of workers | **2 (fixed minimum)** | AWS API hard minimum: 1 worker fails validation (driver + 1 executor required). **`G.025X` is streaming-only and cannot be used here.** ADR-020. |
| Auto-scaling | **disabled** | For sub-2-min batch jobs, scaling-analysis overhead adds wall-clock time, not reduces it. ADR-020. |
| Job bookmark | **disabled** | idempotency handled by ledger + MERGE, not bookmarks |
| Delta enablement | `--datalake-formats delta` | Glue-native Delta |
| `--conf` | `spark.sql.extensions=io.delta.sql.DeltaSparkSessionExtension`, `spark.sql.catalog.spark_catalog=org.apache.spark.sql.delta.catalog.DeltaCatalog` | Delta SQL |
| Timeout | per job (e.g. 30 min) | bounded; SF also enforces (`error_handling.md`) |
| Max retries (Glue) | 0 | retries owned by Step Functions for clean control |
| Temp dir / spark UI logs | artifacts bucket | observability |

> ⚠️ **DynamicFrame prohibition (ADR-019).** Glue's `DynamicFrame` API does **not** support
> Delta Lake. All reads and writes in any job touching Delta **must** use native Spark
> DataFrames — `spark.read.format("delta")` / `df.write.format("delta")` / `DeltaTable.*`.
> Using `create_dynamic_frame.from_catalog` on a Delta table silently bypasses the
> transaction log. This is an execution blocker, not a preference.

## 3. Job parameters (contract with the orchestrator)

`ingest_<dataset>` receives:
- `--dataset` (products|orders|order_items)
- `--batch_id`
- `--staging_uri` (the normalized Parquet path)
- `--source_file` (original raw key, for audit + archival)
- `--env` (dev|prod)
- `--reject_threshold` (default 0.05)

It returns (via job run output / ledger update): `rows_in`, `rows_valid`,
`rows_rejected`, `reject_rate`, `dedup_collapsed`, `status`.

## 4. Entry-point shape (non-binding sketch)

```python
# glue_jobs/ingest.py  (thin entrypoint)
from lakehouse import config, io, transforms, validation, merge, ledger, logging_utils

def main(args):
    spark = io.delta_session()
    ds = args["dataset"]; bid = args["batch_id"]
    raw = io.read_staging(spark, args["staging_uri"], config.SCHEMA[ds])
    typed = transforms.enforce_types(raw, ds)
    derived = transforms.derive(typed, ds, bid, args["source_file"])
    valid, rejected = validation.apply_rules(derived, config.RULES[ds])
    valid = validation.referential_integrity(spark, valid, ds)        # facts only
    deduped = transforms.dedup(valid, config.MERGE_KEY[ds])
    io.write_quarantine(rejected, ds, bid)
    metrics = logging_utils.compute_metrics(raw, deduped, rejected)
    logging_utils.gate(metrics, args["reject_threshold"])             # fail if breach
    merge.upsert(spark, deduped, config.TABLE_PATH[ds], config.MERGE_KEY[ds])
    ledger.mark_loaded(bid, ds, metrics)
```

The heavy logic is all in the importable library → unit-testable without Glue/AWS.

## 5. Dependency management
- Library packaged as a wheel uploaded to the artifacts bucket; referenced via
  `--additional-python-modules` or `--extra-py-files`.
- Pinned versions in `requirements.txt`/`pyproject.toml`; same versions used in CI tests
  so local == Glue behavior.

## 6. Why Lambda for normalization (ADR-011, revised — review 1.1)
A ~1.6 MB spreadsheet doesn't justify a Spark cluster — and at this size it doesn't justify
a Glue Python-shell DPU-minute either. **AWS Lambda** with pandas/openpyxl (packaged as a
layer or container image; memory up to 10 GB) starts in milliseconds, bills in
millisecond increments (~95% cheaper than a Python-shell minimum DPU-minute), isolates the
brittle Excel reader, and keeps the Spark job format-agnostic. If a future source file
exceeds Lambda's limits (15-min / ephemeral-storage), fall back to a Glue Python-shell job
for that dataset — same code, different host.

## 7. Optimize/maintenance job
`optimize_<dataset>` runs table-wide `OPTIMIZE ZORDER BY (...)` (tables are unpartitioned,
ADR-005 revised) + periodic `VACUUM` and `ANALYZE`/statistics where supported. Scheduled
post-load and/or weekly (`delta_lake_design.md` §5–6).

> **Lake Formation note (ADR-019, review R2-1.1):** if Delta tables are governed by AWS Lake
> Formation, catalog-interface operations are limited to read/append/overwrite. `VACUUM` and
> `OPTIMIZE` must be called directly via Spark SQL or the DeltaTable API (not via catalog
> table methods) to avoid permission errors.

## 8. Cost controls
- **2 workers fixed, auto-scaling off** (ADR-020): avoids auto-scaling analysis overhead
  on short batch jobs; 2 workers is the minimum valid Spark configuration.
- Job bookmarks off, short timeouts, staging lifecycle expiry, and `OPTIMIZE ZORDER` to
  keep Athena scan cost low.

## 9. Acceptance criteria
- Parameterized Spark job runs all three datasets from one codebase.
- Library functions unit-tested independently of Glue.
- Job emits the documented metrics and updates the ledger.
