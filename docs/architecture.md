# Architecture & Design Contract

> **Status:** Planning (no code/infra in this phase)
> **Role:** Senior Data Engineer
> **Scope:** Production-grade Lakehouse for e-commerce transactions on AWS.

This document is the **single source of truth (SoT)** for the whole project. Every
other document in `docs/` references the *Design Contract* in §3. If a sub-plan ever
disagrees with this file, **this file wins** and the sub-plan is wrong and must be fixed.

---

## 1. System Overview

We are building a **medallion-style Lakehouse** on AWS that ingests raw e-commerce
transactional files from Amazon S3, normalizes and validates them, writes
ACID-compliant **Delta Lake** tables to a curated zone, registers them in the
**Glue Data Catalog**, and exposes them to **Amazon Athena** for analytics. The
lifecycle is orchestrated by **AWS Step Functions** and deployed via **GitHub Actions**.

Business non-functional drivers (from the brief):

- **High data reliability** — ACID writes, idempotent re-runs, no partial commits.
- **Schema enforcement** — bad/changed schemas are rejected, not silently coerced.
- **Freshness** — each new monthly drop is queryable in Athena shortly after arrival.

## 2. High-Level Data Flow

```
                         ┌─────────────────────────────────────────────────────────┐
                         │                  AWS Step Functions                      │
                         │            (orchestrates the whole lifecycle)            │
                         └───────────────┬───────────────────────────┬─────────────┘
                                         │                           │
  (1) File lands           (2) Normalize  (3) Validate + Transform    (4) Catalog + verify
      in RAW                   xlsx→parquet    + MERGE into Delta          + archive
                                         │                           │
┌──────────┐   S3 event   ┌──────────┐  ┌──────────┐   Delta MERGE  ┌──────────────┐   Glue     ┌─────────┐
│  RAW     │ ───────────▶ │ STAGING  │─▶│  Glue +  │ ─────────────▶ │  DWH (Delta) │ ────────▶  │ Athena  │
│  zone    │              │normalized│  │  Spark   │                │  curated zone│   Catalog  │ queries │
└────┬─────┘              └──────────┘  └────┬─────┘                └──────────────┘            └─────────┘
     │                                       │ reject
     │ on success                            ▼
     ▼                                  ┌──────────┐
┌──────────┐                            │QUARANTINE│  (rejected records + reasons)
│ ARCHIVE  │                            └──────────┘
└──────────┘

Control plane (cross-cutting): DynamoDB ingestion ledger + watermarks (idempotency),
CloudWatch (logs/metrics/alarms), SNS (alerts), IAM (least privilege).
```

Zone semantics (medallion mapping):

| Medallion | This project | Format | Purpose |
|-----------|--------------|--------|---------|
| Bronze | `raw` + `staging` | original `.xlsx`/`.csv` → normalized Parquet | immutable landing + machine-readable normalization |
| Silver | `dwh` (Delta) | Delta Lake | cleaned, deduplicated, schema-enforced, merged |
| Gold | Athena views / curated marts | Delta / views | analytics-ready aggregates (future) |

---

## 3. THE DESIGN CONTRACT (authoritative)

### 3.1 Global naming & region

- **Region:** `us-east-1` (assumption — see `decision.md` ADR-009).
- **Project prefix:** `ecom-lakehouse` for resources, `ecom_lakehouse` for catalog/db objects.
- **Conventions:** S3 buckets & IAM roles `kebab-case`; tables/columns `snake_case`;
  environment suffix `-{dev|prod}` on every stateful resource.

### 3.2 S3 zones (one bucket per zone, env-suffixed)

| Logical zone | Bucket name | Retention | Notes |
|--------------|-------------|-----------|-------|
| Raw landing | `ecom-lakehouse-raw-{env}` | S3 Standard (Intelligent-Tiering optional) | original drops, immutable, versioned |
| Staging/normalized | `ecom-lakehouse-staging-{env}` | 7d (lifecycle expiry) | xlsx/csv → Parquet, ephemeral |
| Curated DWH | `ecom-lakehouse-dwh-{env}` | indefinite | **Delta tables** ("lakehouse-dwh" zone from brief) |
| Archive | `ecom-lakehouse-archive-{env}` | S3 Standard (Intelligent-Tiering optional) | post-ingest originals, `/<dataset>/<batch_id>/` |
| Quarantine | `ecom-lakehouse-quarantine-{env}` | 180d | rejected records + reject reason |
| Athena results | `ecom-lakehouse-athena-results-{env}` | 30d | query output (workgroup-bound) |
| Artifacts | `ecom-lakehouse-artifacts-{env}` | indefinite (versioned) | Glue scripts, JARs, SF definitions |

Raw key layout: `s3://…-raw-{env}/<dataset>/<yyyy>/<mm>/<filename>`
e.g. `…/orders/2025/04/orders_apr_2025.xlsx`.

### 3.3 Canonical datasets, schemas & dtypes

Profiled from the provided files (`products.csv`, `orders_apr_2025.xlsx`,
`order_items_apr_2025.xlsx`). These are the **enforced** Delta schemas.

**`dim_products`** (dimension; source `products.csv`)

| column | Spark type | Glue/Athena type | constraint |
|--------|-----------|------------------|------------|
| `product_id` | `IntegerType` | `int` | **PK**, not null, unique |
| `department_id` | `IntegerType` | `int` | not null |
| `department` | `StringType` | `string` | not null; in {Books, Sports, Toys, Home, Clothing, Electronics} |
| `product_name` | `StringType` | `string` | not null |

**`fct_orders`** (fact; source `orders_apr_2025.xlsx`)

| column | Spark type | Glue/Athena type | constraint |
|--------|-----------|------------------|------------|
| `order_num` | `IntegerType` | `int` | not null |
| `order_id` | `LongType` | `bigint` | **PK / merge key**, not null, unique |
| `user_id` | `LongType` | `bigint` | not null |
| `order_timestamp` | `TimestampType` | `timestamp` | not null, valid, ≤ now |
| `total_amount` | `DecimalType(10,2)` | `decimal(10,2)` | not null, ≥ 0 |
| `order_date` | `DateType` | `date` | **Z-Order key** (partition-ready at scale, ADR-005); derived from `date`/`order_timestamp` |

**`fct_order_items`** (fact; source `order_items_apr_2025.xlsx`)

| column | Spark type | Glue/Athena type | constraint |
|--------|-----------|------------------|------------|
| `id` | `LongType` | `bigint` | **PK / merge key**, not null, unique |
| `order_id` | `LongType` | `bigint` | FK → `fct_orders.order_id`, not null |
| `user_id` | `LongType` | `bigint` | not null |
| `days_since_prior_order` | `IntegerType` | `int` | 0–365 (observed 0–30) |
| `product_id` | `IntegerType` | `int` | FK → `dim_products.product_id`, not null |
| `add_to_cart_order` | `IntegerType` | `int` | ≥ 1 |
| `reordered` | `IntegerType` | `int` (or `boolean`) | ∈ {0,1} |
| `order_timestamp` | `TimestampType` | `timestamp` | not null |
| `order_date` | `DateType` | `date` | **Z-Order key** (partition-ready at scale, ADR-005) |

**Audit columns appended to every Delta table** (governance):
`_ingest_ts` (timestamp), `_source_file` (string), `_batch_id` (string),
`_record_hash` (string, sha-256 of business columns — used for dedup).

### 3.4 Keys, partitioning, merge strategy

| table | merge key (upsert) | partition (current) | dedup rule | layout |
|-------|--------------------|---------------------|------------|--------|
| `dim_products` | `product_id` | none (≈1k rows) | keep latest by `_ingest_ts` | unpartitioned + `OPTIMIZE ZORDER BY (department)` |
| `fct_orders` | `order_id` | **none** (Z-Order by `order_date`) | keep latest by `_record_hash`/`_ingest_ts` | unpartitioned + `OPTIMIZE ZORDER BY (order_date)` |
| `fct_order_items` | `id` | **none** (Z-Order by `order_date`,`product_id`) | keep latest by `id` | unpartitioned + `OPTIMIZE ZORDER BY (order_date, product_id)` |

**Partitioning decision (revised — ADR-005):** at the current data volume (~500 orders /
~2,768 items per monthly drop, all sharing one `order_date` per file), physical
partitioning by `order_date` would create many tiny files and hurt Athena/Spark
performance. We therefore keep all three tables **unpartitioned** and use Delta
**Z-ORDER + data skipping** as the performance mechanism now, with a **documented
volume-based promotion path** to physical `order_date` partitioning once a partition
would hold a meaningful amount of data. The partitioning *logic* (key choice, threshold,
trade-offs) is specified in `delta_lake_design.md` §3 — this satisfies the brief's
"Partitioning for performance" + "justify your partitioning logic" as a deliberate
decision, not a reflex. See `delta_lake_design.md` for OPTIMIZE/VACUUM and small-file
management.

### 3.5 Glue Data Catalog & Athena

- Glue database: `ecom_lakehouse_db_{env}`.
- Tables registered: `dim_products`, `fct_orders`, `fct_order_items` (Delta-aware).
- Athena workgroup: `ecom_lakehouse_wg_{env}` (results → Athena results bucket, enforced).

### 3.6 DynamoDB control plane

| table | PK | SK | purpose |
|-------|----|----|---------|
| `ecom_lakehouse_ingestion_ledger_{env}` | `file_key` | — | idempotency: one row per ingested file (status, checksum, row counts) |
| `ecom_lakehouse_watermarks_{env}` | `dataset` | — | last processed batch/date per dataset |

> **Removed (review 1.5):** the DynamoDB schema-registry table was dropped. Expected
> schemas live in code (`src/lakehouse/schemas.py`, mirroring §3.3) and are enforced
> structurally at normalization and definitively by Delta on write — see `decision.md`
> ADR-006 (revised).

See `dynamodb_schema.md`. Justification for introducing DynamoDB (not in brief's core
list) is recorded in `decision.md` ADR-006.

### 3.7 Identifiers

- **`batch_id`** = `{dataset}-{yyyymmdd}-{short_uuid}` — stamped at ingest, threaded
  through ledger, audit columns, archive path, and logs for end-to-end traceability.

---

## 4. Component Responsibilities (RACI-lite)

| Component | Responsibility | Detailed in |
|-----------|----------------|-------------|
| S3 zones | storage & lifecycle | `data_handling.md` |
| AWS Lambda | xlsx/csv → Parquet normalization (ADR-011 revised) | `data_handling.md`, `glue_jobs.md` |
| Glue + Spark + Delta | validation, transform, dedup, MERGE | `transformation_logic.md`, `delta_lake_design.md`, `data_validation.md` |
| DynamoDB | idempotency (ledger) + watermarks | `dynamodb_schema.md` |
| Step Functions | orchestration, branching, retries, timeouts | `orchestration_stepfunctions.md` |
| Glue Catalog + Athena | metadata + query | `catalog_and_athena.md` |
| SNS + CloudWatch | alerting, observability | `error_handling.md`, `monitoring_observability.md` |
| Terraform | all infra as code | `terraform.md` |
| GitHub Actions | CI/CD | `cicd_github_actions.md` |
| IAM/KMS | security, least privilege | `security_iam.md` |

## 5. Key Architectural Principles

1. **Idempotency first** — any file can be re-processed safely (ledger + MERGE).
2. **Immutable raw** — never mutate landed files; transformations are downstream.
3. **Schema-on-write at Silver** — Delta enforces schema; drift is rejected, not coerced.
4. **Separation of zones** — failure in one zone never corrupts another.
5. **Everything reproducible** — infra (Terraform) and pipeline (Glue/SF) are versioned
   and deployed only through CI/CD on `main`.
6. **Observable & alertable** — every state transition emits logs/metrics; failures page.

---

## 6. Document Map

See `master_plan.md` §Document Index for the full list and reading order. When an
executing agent gets stuck, `reference.md` is the resource hub — it routes any symptom to
the owning document and the authoritative external reference.
