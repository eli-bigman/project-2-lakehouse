<!-- ai-noindex -->

# Master Architecture & Project Documentation
## E-Commerce Lakehouse on AWS — Learning & Interview Guide

> **For:** Junior engineers learning AWS + Terraform through this project.
> **Project phase:** Planning complete. Implementation starts at Sprint 1.
> **Verified against:** actual source data files + all planning docs as of 2026-06-01.

---

## SECTION 1 — The Big Picture: What, Why, and the Agentic Approach

### 1.1 What Does This System Do?

Imagine you run an online store. Every month, your sales system dumps three spreadsheet
files into a shared folder:

- `products.csv` — your product catalogue (1,000 products across 6 departments)
- `orders_apr_2025.xlsx` — 500 customer orders placed in April 2025
- `order_items_apr_2025.xlsx` — 2,768 line items within those orders (the individual
  products each customer added to their cart)

> **The real data, confirmed:** April's order file contains exactly 500 rows, all dated
> `2025-04-01`, with `order_id` values 10,000–10,499. The items file has 2,768 rows.
> All are clean now — but they won't always be. The pipeline is built to handle dirt.

The problem: those raw `.xlsx` files are messy to query, have no guarantees of quality,
and can't be used directly by analysts. Our system takes those monthly drops and turns
them into a **clean, reliable, query-ready data warehouse** accessible through
Amazon Athena — without an analyst ever writing a single `VLOOKUP`.

### 1.2 The Core Goal and Business Use Case

**Goal in one sentence:** Automatically ingest, clean, deduplicate, and store raw
e-commerce transaction files so analysts can query reliable data in minutes, not days.

**Real-world value this creates:**
- An analyst can open Athena and run `SELECT SUM(total_amount) FROM fct_orders WHERE order_date = '2025-04-01'` and get the answer in seconds.
- A data steward gets an automatic alert if a bad file drops — with the exact rejection reason.
- An engineer re-running a failed job won't create duplicate records in the warehouse.
- A stakeholder asking "how fresh is the data?" gets a clear, measurable answer.

### 1.3 The "Agentic Approach" — How AI Agents Interact With This

**The plain-English version:** Instead of one human (or one AI) knowing everything and
doing everything, we break every task into a self-contained, step-by-step unit. Each
unit says: here are my **inputs**, here's the **action** to take, here's the
**expected output**, and here's **how to verify** it worked. Then it passes the baton.

Think of it like a relay race where each runner gets a baton and a card that says
exactly what route to run — they don't need to know what the previous runner did.

**Why this matters for an AI agent working on this project:**
1. An AI agent can pick up any planning document in `docs/` and execute its steps
   without needing to read every other document first.
2. The `batch_id` identifier (`products-20250401-abc123`) is threaded through every
   layer — S3 paths, DynamoDB ledger, audit columns, archive paths, and logs — so any
   agent can trace a piece of data end-to-end.
3. Every decision that was made (and why) is recorded in `docs/decision.md` as an ADR
   (Architecture Decision Record), so an agent never has to guess the intent.

**In the infrastructure:** The Terraform plan in `terraform.md` is written with this
same discipline — each provisioning step lists inputs → action → output → verification,
so a human or agent can execute it without needing extra context.

---

## SECTION 2 — AWS Component Breakdown: The Learning Hub

Every AWS service used in this project is explained below with an analogy, its exact
technical role here, and how it connects to everything else.

---

### 2.1 Amazon S3 (Simple Storage Service)

**The Analogy:** S3 is like a set of **filing cabinets in a secure warehouse**. Each
cabinet is a *bucket*. Files inside are *objects*. The warehouse enforces rules: some
cabinets are locked (encrypted), some have auto-shredding after 7 days (lifecycle),
and some have a history book of every version ever put in them (versioning).

**The Technical Role in Our Pipeline:**
We use **7 separate S3 buckets** — one per logical zone, each named
`ecom-lakehouse-{zone}-{env}`:

| Bucket | Zone | What lives there | Lifetime |
|--------|------|-----------------|---------|
| `-raw-dev` | Bronze | Original uploaded `.xlsx`/`.csv` files — never touched after landing | Indefinite, versioned |
| `-staging-dev` | Bronze | Lambda-normalized Parquet files — temporary working copy | 7 days then auto-deleted |
| `-dwh-dev` | Silver | **Delta Lake tables** — the clean, queryable warehouse | Indefinite, VACUUM-managed |
| `-archive-dev` | Bronze | Original files moved here after successful processing | Indefinite |
| `-quarantine-dev` | — | Rejected rows + rejection reasons | 180 days |
| `-athena-results-dev` | — | Athena query output files | 30 days |
| `-artifacts-dev` | — | Glue Python scripts, Lambda packages, Step Functions definitions | Indefinite, versioned |

**How It Connects to Other Services:**
- EventBridge listens to S3's raw bucket for `Object Created` events → triggers Step Functions.
- Lambda reads from raw, writes to staging.
- Glue reads from staging, reads/writes the Delta tables in dwh.
- Athena reads from dwh and writes query results to athena-results.
- Every bucket encrypts with KMS (via `EnforceKMSEncryption` bucket policy — see §2.10).

---

### 2.2 AWS Lambda

**The Analogy:** Lambda is a **vending machine**. You press a button (an event arrives),
it does exactly one thing (your function runs), and then it shuts off. You don't pay
for it while it's sitting idle, and you never manage the machine itself — AWS does.

**The Technical Role in Our Pipeline:**
Lambda handles the **lightweight, fast tasks** that don't need a Spark cluster:

| Lambda function | What it does | Why Lambda and not Glue |
|-----------------|-------------|------------------------|
| `normalize_to_parquet` | Reads `.xlsx` or `.csv` using pandas + openpyxl, validates column names against the in-code schema, writes Parquet to staging | A 1.6 MB spreadsheet doesn't need a cluster. Lambda starts in milliseconds and costs ~95% less than a Glue Python-shell DPU-minute. |
| `claim_file` | Writes a conditional-put to DynamoDB ledger — "I'm processing this file now." If already processed, the whole pipeline short-circuits. | Fast, cheap, needs strong-consistency write — perfect Lambda use case. |
| `validate_schema` | Compares staging Parquet's column set to `lakehouse.schemas` — catches structural drift before Spark runs | Cheap defense-in-depth gate between normalization and ETL. |
| `archive_file` | Moves the original from raw to archive, marks ledger `status=ARCHIVED` | Simple file move + DynamoDB update — no cluster needed. |

**How It Connects:**
Step Functions calls Lambda functions directly (synchronous task). Lambda assumes its
IAM role (e.g. `normalize-lambda-role`) which has permission to read `raw`, write
`staging`, and put one item in the DynamoDB ledger — nothing else.

> **ADR-019 in practice:** Lambda cannot replace Glue for the Delta MERGE step because
> Glue's DynamicFrame API doesn't support Delta Lake — all Delta reads/writes must use
> native Spark DataFrames.

---

### 2.3 AWS Glue (ETL + Data Catalog)

**The Analogy (ETL jobs):** Glue is the **factory floor**. Raw materials (Parquet files)
come in on a conveyor belt, workers (Spark executors) clean, transform, and quality-check
them, and finished products (clean Delta table rows) come out the other end.

**The Analogy (Data Catalog):** The Catalog is the factory's **parts catalogue** — it
tells every system "here's the table called `fct_orders`, here are its columns, here's
where the files are stored." Athena uses this catalogue to know how to query the Delta
tables.

**The Technical Role:**
Three Glue Spark jobs are defined, all backed by one parameterized Python script:

1. **`ingest_products`** — reads `dim_products` staging Parquet → validates → deduplicates → MERGEs into Delta
2. **`ingest_orders`** — reads `fct_orders` staging Parquet → validates → deduplicates → MERGEs into Delta
3. **`ingest_order_items`** — reads `fct_order_items` staging Parquet → validates → referential integrity check → deduplicates → MERGEs

**Exact configuration (confirmed from source):**
- Glue version: **4.0+** (first version with native Delta Lake support)
- Worker type: **`G.1X`** (0.25 vCPU, 4 GB RAM per worker)
- Workers: **exactly 2** (AWS hard minimum — 1 worker fails API validation)
- Auto-scaling: **off** (for <2-minute batch jobs, scaling analysis adds overhead, not saves it)
- Job bookmarks: **off** (idempotency is handled by DynamoDB ledger + Delta MERGE)

**The Glue Data Catalog:**
One database: `ecom_lakehouse_db_dev`. Three tables: `dim_products`, `fct_orders`,
`fct_order_items`. Registered as native Delta (`table_type=DELTA`), which means Athena
reads the Delta transaction log directly — no `MSCK REPAIR TABLE`, no symlink manifests.

---

### 2.4 Delta Lake (Table Format)

**The Analogy:** Delta Lake is a **ledger book** on top of S3. Normal Parquet files are
like loose paper — you write them, but there's no record of changes, no way to roll
back, and no prevention of two writers corrupting the same file simultaneously. Delta
wraps those Parquet files in an ACID transaction log (`_delta_log/`) that tracks every
write, allowing rollback, upserts, and consistent reads.

**The Technical Role:**
Delta gives us four critical properties for this pipeline:

1. **ACID transactions** — a failed write is rolled back. The warehouse is never in a
   half-written state.
2. **`MERGE` / upsert** — when we re-process a file (or process a correction), we update
   existing rows rather than duplicating them. Merge key: `order_id` for orders, `id`
   for items, `product_id` for products.
3. **Schema enforcement** — if a new file has a column type that doesn't match what we
   defined, Delta rejects the write with a clear error. Analysts never silently get the
   wrong data type.
4. **Time travel** — if a bad batch loads, we can run `RESTORE TABLE fct_orders VERSION AS OF 5` to roll back to before the bad data. This is the production rollback mechanism.

**What the three tables look like (from the actual data):**

`dim_products`: 1,000 rows. 4 columns. One row per product.
`fct_orders`: 500 rows per monthly drop. 6 business columns + 4 audit columns.
`fct_order_items`: 2,768 rows per monthly drop. 9 business columns + 4 audit columns.

All three tables are **unpartitioned** at current volume. Instead of physical date
directories, Delta's **Z-ORDER** clusters rows by `order_date` so queries that filter
by date skip non-matching files — same speed benefit, without the small-file penalty
that partitioning would create at 500 rows/month.

---

### 2.5 AWS Step Functions

**The Analogy:** Step Functions is the **project manager**. It doesn't do any work
itself — it tells Lambda and Glue when to start, watches their progress, retries them
if they fail, branches to different paths based on outcomes, and sends an alert when
something goes wrong. It keeps a full visual execution history in the AWS console.

**The Technical Role:**
Our state machine has 9 states that execute in order for every new file drop:

```
[ClaimFile] → [Normalize] → [ValidateSchema] → [IngestProducts]
→ [IngestOrders] → [IngestOrderItems] → [QualityGate] → [Optimize]
→ [UpdateCatalog] → [AthenaValidate] → [Archive] → [Succeed]
```

Key branching logic:
- **ClaimFile fails** (DynamoDB says file already processed) → `NoOpSucceed` — safe exit, no duplicates
- **ValidateSchema fails** (wrong columns) → `HandleFailure` — alert, do not process
- **QualityGate fails** (reject rate > 5%) → `HandleFailure` — alert, keep quarantine for inspection
- Any Glue job failure → **3 retries with exponential backoff** (30s, 60s, 120s) before `HandleFailure`

**Why Standard, not Express (ADR-010):**
Glue jobs take minutes. Express Workflows have a **5-minute hard timeout** and
**at-least-once** execution (can run a step twice!). Standard Workflows run for up to
a year and guarantee **exactly-once** execution — critical when we're writing to a
financial data warehouse. At ~monthly cadence, the cost difference is $0.15/month.

---

### 2.6 Amazon DynamoDB (Control Plane)

**The Analogy:** DynamoDB is the **receptionist's logbook**. Every time a file enters
the building, the receptionist writes its name, the time it arrived, and its current
status. If the same file tries to enter again, the receptionist checks the logbook and
says "you're already in here — no entry." It's the system's memory.

**The Technical Role:**
We use exactly **two DynamoDB tables** (a third, schema registry, was removed in ADR-006
because Delta Lake already enforces schemas):

**Table 1: `ecom_lakehouse_ingestion_ledger_dev`**
One row per source file. Primary key: `file_key` (the S3 object key).
Tracks: `batch_id`, `dataset`, `source_checksum`, `status`, `rows_in`, `rows_valid`,
`rows_rejected`, `reject_rate`, `staging_uri`, `archive_uri`, `error`.

Status lifecycle: `NORMALIZED` → `VALIDATED` → `LOADED` → `ARCHIVED` (or `FAILED`)

The idempotency pattern uses a **conditional write**: the `ClaimFile` Lambda puts an item
only if `attribute_not_exists(file_key) OR status = 'FAILED'`. If the condition fails
(already processed), it raises `ConditionalCheckFailedException` → Step Functions
routes to `NoOpSucceed`. This guarantees a file is never double-processed.

**Table 2: `ecom_lakehouse_watermarks_dev`**
Three rows — one per dataset (`products`, `orders`, `order_items`). Tracks the last
successfully processed period and batch ID. Used for freshness monitoring and backfill
decisions.

Both tables use: on-demand billing (PAY_PER_REQUEST), SSE-KMS, point-in-time recovery.

---

### 2.7 Amazon Athena (Query Engine)

**The Analogy:** Athena is a **librarian who can read directly from the filing cabinet**.
You don't need to move the books to a separate database — Athena reads the Delta files
directly from S3, using the Glue Catalog to know where everything is. You pay only for
the data you read, and the query result is written back to an S3 results bucket.

**The Technical Role:**
- Downstream analysts query `dim_products`, `fct_orders`, `fct_order_items` using
  standard SQL — no data movement required.
- Uses **Athena engine v3** (native Delta Lake support) — reads the `_delta_log`
  directly without symlink manifests or `MSCK REPAIR`.
- Workgroup `ecom_lakehouse_wg_dev` enforces that results go to the `athena-results`
  bucket (cost and security control).
- After each pipeline run, an optional `AthenaValidate` Step Functions state runs
  `SELECT COUNT(*) FROM fct_orders` — if the count doesn't match the ledger's
  `rows_valid`, the pipeline fails with an alert.

---

### 2.8 Amazon EventBridge

**The Analogy:** EventBridge is a **security camera with an alarm**. When it sees a new
object appear in the raw S3 bucket (`Object Created` event), it automatically fires a
signal to Step Functions: "New file just landed — start the pipeline."

**The Technical Role:**
Two EventBridge rules:
1. **Primary trigger:** S3 `Object Created` → immediately starts a Step Functions
   execution with the S3 key as input.
2. **Fallback / backfill trigger:** A scheduled rule that enumerates raw keys and fans
   them through the pipeline via a Step Functions `Map` state — used for historical
   backfill or when the real-time trigger needs to be "simulated."

---

### 2.9 AWS IAM (Identity and Access Management)

**The Analogy:** IAM is the **company's ID badge system**. Every person (human) and
every machine (Lambda, Glue, Step Functions) has a badge. Each badge specifies exactly
which rooms they can enter and what they can do there — and nothing else. A Lambda that
normalizes files can read from `raw` and write to `staging`, but its badge doesn't open
the door to `dwh` or `archive`.

**The Technical Role:**
Six runtime roles, each scoped to the minimum necessary permissions:

| Role | Can do | Cannot do |
|------|--------|-----------|
| `normalize-lambda-role` | Read `raw`, write `staging`, DynamoDB put | Touch `dwh`/`archive` |
| `glue-ingest-role` | Read `staging`, read/write `dwh`, write `quarantine`, DynamoDB read/write ledger | Delete `raw` or write `artifacts` |
| `archive-lambda-role` | Read `raw`, write `archive`, delete from `raw`, update ledger | Write `dwh` |
| `stepfunctions-role` | Start Glue jobs, invoke Lambda, run Athena, publish SNS | Write S3 data directly |
| `athena-role` | Query catalog tables, write results bucket | Write `dwh` |
| `gha-deploy-role` | Terraform-manage infrastructure, upload artifacts | Any runtime data access |

**GitHub Actions uses OIDC** — not long-lived access keys. GitHub proves "I'm running
on the `main` branch of `your-org/ecom-lakehouse`" and gets a short-lived token. No
`AWS_ACCESS_KEY_ID` stored in GitHub Secrets.

---

### 2.10 AWS KMS (Key Management Service)

**The Analogy:** KMS is the **master locksmith's key cabinet**. Every encrypted filing
cabinet (S3 bucket) has a lock. KMS manages the master keys that open those locks. You
never see the raw key material — you just tell KMS "use key X to encrypt/decrypt this."

**The Technical Role:**
- In `prod`: Customer-Managed Keys (CMKs) — one per data class (S3, DynamoDB, logs).
  CMKs let you control who can use the key, rotate it, and audit every use.
- In `dev`: AWS-managed keys are acceptable (saves ~$1/key/month in the learning env).
- **S3 Bucket Keys** are enabled (`bucket_key_enabled = true`) on every bucket — this
  caches the data encryption key in S3, reducing KMS API calls by ~99% and slashing cost.
- Two bucket policy `Deny` statements enforce encryption (ADR-021):
  - `EnforceHTTPS`: blocks any non-TLS request to the bucket.
  - `EnforceKMSEncryption`: blocks any `PutObject` that doesn't explicitly specify
    `aws:kms` encryption. Without this, a client can override the bucket default by
    sending `AES256` in the upload header — silently bypassing KMS.

---

### 2.11 Amazon CloudWatch + Amazon SNS (Observability & Alerting)

**The Analogy:** CloudWatch is the **factory's control panel** — it shows gauges for
every machine (metrics), stores a log of every event (logs), and has alarm lights that
turn red when something goes wrong (alarms). SNS is the **factory's PA system** — when
an alarm fires, SNS broadcasts an alert to whoever is subscribed (email, Slack, PagerDuty).

**Custom metrics emitted per batch:**
`rows_in`, `rows_valid`, `rows_rejected`, `reject_rate`, `pipeline_failures`,
`freshness_lag_hours`, `batch_duration_seconds`, `dedup_collapsed`, `fk_orphans`

**Alarms that fire to SNS:**
- `pipeline_failures > 0` → CRITICAL
- `reject_rate > 0.05` → WARNING
- `freshness_lag_hours > SLA` → CRITICAL (catches a missing drop, not just a broken one)
- `dlq_depth > 0` → CRITICAL (means an EventBridge event was dropped)

---

### 2.12 GitHub Actions (CI/CD)

**The Analogy:** GitHub Actions is an **automated quality gate at the factory entrance**.
Every time code is changed, the gate automatically runs tests, checks for security issues,
and (on `main` only) deploys the changes. Nothing reaches production without passing
the gate.

**Two workflows:**
1. **`ci.yml`** (every pull request + every branch push):
   lint → security scan (`detect-secrets`, `tfsec`) → unit tests (pytest + chispa) →
   integration tests → `terraform validate + plan`
2. **`deploy.yml`** (only `main` branch):
   OIDC auth → build+upload Lambda/Glue artifacts → `terraform apply` →
   register Step Functions ASL → smoke test → tag release

---

### 2.13 Streamlit (Internal UI — Sprint 5)

A four-page internal web app for testing all 8 user stories interactively. It connects
to Athena, DynamoDB, Step Functions, and CloudWatch using your AWS credentials. Not a
customer-facing product — a developer/analyst tool.

Pages: Pipeline Dashboard, Data Explorer, Data Quality Browser, Batch Trigger.

---

## SECTION 3 — Data Flow & Lifecycle: The Journey of a Row

Let's follow `order_id = 10000` from `orders_apr_2025.xlsx` as it moves through the system.

---

### Step 1 — File Lands in Raw

**Event:** Someone (or a scheduled job) uploads `orders_apr_2025.xlsx` to
`s3://ecom-lakehouse-raw-dev/orders/2025/04/orders_apr_2025.xlsx`.

**What happens automatically:**
- S3 fires an `Object Created` event to EventBridge.
- EventBridge starts a Step Functions execution with `{"raw_key": "orders/2025/04/orders_apr_2025.xlsx"}`.
- The raw file is **never modified** — it's immutable and versioned.

---

### Step 2 — ClaimFile (Idempotency Gate)

**State: ClaimFile**
Lambda does a DynamoDB **conditional put** on the ledger:
- If `file_key` doesn't exist → write `status=CLAIMED` → continue.
- If `file_key` exists and `status=ARCHIVED` → `ConditionalCheckFailedException` → execution ends at `NoOpSucceed`. No duplicate processing.

*Our order `10000` is safe: it won't be processed twice.*

---

### Step 3 — Normalize (xlsx → Parquet)

**State: Normalize**
Lambda runs with pandas + openpyxl:
1. Opens the `.xlsx` file from S3.
2. Confirms the columns match exactly: `[order_num, order_id, user_id, order_timestamp, total_amount, date]`.
3. Writes `part.parquet` to `s3://ecom-lakehouse-staging-dev/orders/batch_id=orders-20250401-abc123/`.
4. Updates ledger: `status=NORMALIZED`, `source_checksum=<sha256>`, `rows_in=500`.

*The Parquet file has no types inferred by pandas — it's written with explicit dtypes
that match what Spark will expect.*

---

### Step 4 — ValidateSchema (Defense-in-Depth)

**State: ValidateSchema**
A second Lambda checks the Parquet file's schema against `lakehouse.schemas`. Redundant
with Step 3, but cheap and safe — a malformed staging artifact can never reach the MERGE.

---

### Step 5 — LoadDatasets (The Heavy Work)

**States: IngestProducts → IngestOrders → IngestOrderItems (sequential)**

For `IngestOrders`, the parameterized Glue Spark job (`--dataset orders`) runs:

```
1. spark.read.format("parquet").load(staging_uri)
   → 500 rows, all dates 2025-04-01

2. enforce_types(): cast order_id → LongType, total_amount → Decimal(10,2), etc.

3. derive():
   - order_date = to_date(order_timestamp)   → "2025-04-01"
   - _ingest_ts = current_timestamp()
   - _source_file = "orders/2025/04/orders_apr_2025.xlsx"
   - _batch_id = "orders-20250401-abc123"
   - _record_hash = sha2(concat_ws("||", order_num, order_id, user_id, ...), 256)

4. validate():
   - Rule O1: order_id not null and > 0  ✓ (all 500 pass)
   - Rule O5: order_timestamp parses and ≤ now  ✓
   - Rule O6: total_amount ≥ 0  ✓ (range: $23.23–$498.72)
   → valid: 500, rejected: 0

5. referential_integrity(): (skipped for orders — no upstream FK dependency)

6. dedup(): Window.partitionBy(order_id).orderBy(_ingest_ts.desc()) → row_number=1
   All 500 have unique order_ids → no rows dropped.

7. merge.upsert(): Delta MERGE on order_id
   - First run: all 500 rows are INSERT (table didn't exist) → table created
   - Re-run: same hash → MATCHED but hash unchanged → no row written (skip no-ops)

8. ledger.mark_loaded(): status=LOADED, rows_valid=500, reject_rate=0.0
```

`order_id=10000` is now **safely in the `fct_orders` Delta table** with ACID guarantees.

---

### Step 6 — QualityGate

**State: QualityGate (Choice)**
`reject_rate = 0 / 500 = 0.0`, which is ≤ 0.05 threshold → `true` branch → continue.

If this were > 0.05: `HandleFailure` → CloudWatch logs + ledger `status=FAILED` + SNS email alert.

---

### Step 7 — Optimize + Catalog

**State: Optimize**
Glue runs `OPTIMIZE fct_orders ZORDER BY (order_date)`:
- Compacts small Parquet files into larger ones (~128 MB target).
- Clusters data by `order_date` so queries like `WHERE order_date = '2025-04-01'` skip
  non-matching files — this is how we get partition-like query speed without partitions.

**State: UpdateCatalog**
Registers/updates the table in the Glue Data Catalog with `table_type=DELTA`.

---

### Step 8 — AthenaValidate

**State: AthenaValidate**
Athena runs: `SELECT COUNT(*) FROM fct_orders`
Returns `500` → matches ledger's `rows_valid=500` → continue.

---

### Step 9 — Archive

**State: Archive**
Lambda copies `orders_apr_2025.xlsx` from `raw` to
`s3://ecom-lakehouse-archive-dev/orders/orders-20250401-abc123/orders_apr_2025.xlsx`
and deletes it from `raw`. Ledger: `status=ARCHIVED`.

The staging Parquet expires automatically in 7 days (S3 lifecycle rule).

---

### Step 10 — Analyst Queries Athena

The data is now live. An analyst opens Athena and runs:
```sql
SELECT u.user_id, SUM(o.total_amount) AS total_spend
FROM fct_orders o
JOIN fct_order_items i ON o.order_id = i.order_id
JOIN dim_products p ON i.product_id = p.product_id
WHERE o.order_date = '2025-04-01'
  AND p.department = 'Electronics'
GROUP BY u.user_id
ORDER BY total_spend DESC
LIMIT 10;
```

Athena reads the Delta transaction log, uses Z-Order statistics to skip non-matching
files, joins the three tables, and returns results — all without touching the original
Excel files.

---

### Security at every step

| Layer | Mechanism |
|-------|-----------|
| In transit | All S3, DynamoDB, Athena calls use HTTPS (enforced by `EnforceHTTPS` bucket policy) |
| At rest (S3) | SSE-KMS with CMK + Bucket Keys; `EnforceKMSEncryption` blocks AES256 override |
| At rest (DynamoDB) | SSE-KMS + PITR |
| Identity | Each Lambda/Glue role has only the permissions it needs — nothing more |
| Audit | CloudTrail records every API call to S3, Glue, DynamoDB, Athena |

---

## SECTION 4 — Architectural Decisions: "Why This, Not That"

These are the choices that define this system. For each one, understand the trade-off.

---

### Decision 1: Serverless Compute vs. Provisioned Servers (EC2)

**We chose:** AWS Lambda (for lightweight tasks) + Glue managed Spark (for ETL).
No EC2 instances, no servers to patch or manage.

**Why serverless wins here:**
- **Data volume is small and infrequent.** 500 orders per month (~1.6 MB) runs in seconds. A continuously-running EC2 instance would cost ~$30–50/month sitting idle 99.9% of the time. Lambda/Glue cost cents and run only when needed.
- **No operational burden.** AWS manages the compute, OS patches, and scaling. We focus on the pipeline logic.

**The trade-off — Serverless vs. Provisioned:**
| Dimension | Serverless (our choice) | Provisioned (EC2) |
|-----------|------------------------|------------------|
| Cost at low volume | ✅ Near-zero (pay per use) | ❌ Runs 24/7 regardless |
| Cold start latency | ⚠️ Lambda: ms; Glue: ~2–3 min startup | ✅ Always warm |
| Operational overhead | ✅ Zero | ❌ Patching, scaling, AMIs |
| Maximum job duration | ⚠️ Lambda: 15 min; Glue: configurable | ✅ Unlimited |

**When to switch to EC2/ECS:** If data volume grows to TBs daily and jobs need to run
continuously for hours, a persistent Spark cluster on EMR or EC2 becomes cheaper.

---

### Decision 2: Delta Lake vs. Plain Parquet (or a Traditional Data Warehouse)

**We chose:** Delta Lake on S3.

**Why not plain Parquet?**
Parquet is great for reading but terrible for updating. To "update" a record in a plain
Parquet warehouse, you'd have to rewrite the entire partition. If two jobs write at the
same time, you get corrupted data. There's no rollback.

**Why not a managed warehouse (Redshift)?**
Redshift stores data inside its own managed service — you pay for always-on compute.
Delta Lake on S3 separates storage from compute: data lives in S3 (cheap, infinite)
and compute comes only when you query (Athena, Glue). At our scale, this is much cheaper.

**The trade-off — Delta on S3 vs. Redshift:**
| Dimension | Delta on S3 (our choice) | Redshift |
|-----------|-------------------------|----------|
| Storage cost | ✅ S3 Standard (~$0.023/GB) | ❌ Redshift includes compute |
| Query performance | ⚠️ Athena good for ad-hoc; no sub-second | ✅ Sub-second for complex joins |
| ACID + upserts | ✅ Native MERGE | ✅ Native |
| Operational overhead | ✅ Serverless | ⚠️ Cluster management |
| Lock-in | ✅ Open format, portable | ❌ Proprietary |

---

### Decision 3: Unpartitioned + Z-Order vs. Physical Date Partitioning

**We chose:** No physical partitions. Z-ORDER by `order_date` instead.

**The trade-off — Partitioning vs. Z-Order:**

Physical partitioning means creating S3 folder prefixes like `/order_date=2025-04-01/`.
Athena can skip entire folders (partition pruning) when you filter by date. Sounds great.

The problem: our monthly files are tiny — 500 rows per file. All orders in April's file
have the same `order_date`. If we partition daily, each folder holds only 500 rows in
a few KB of Parquet. S3 charges per GET request for each file, and Delta's metadata
grows. We'd end up paying more to scan less data.

**Z-Order** rearranges rows within a file so that similar `order_date` values are
clustered together. Delta tracks the min/max `order_date` in each file's statistics.
When Athena queries `WHERE order_date = '2025-04-01'`, Delta checks those statistics
and skips files that can't possibly contain that date — same pruning benefit, no
fragmentation.

**Promotion rule (documented in ADR-005):** Switch to physical partitioning when a
single date holds ≥ 1 GB of data. At that volume, the pruning benefit outweighs the
cost of extra files. We're at ~1 MB/month now.

---

## SECTION 5 — Terraform 101: Infrastructure as Code Strategy

### 5.1 What is Terraform and Why Do We Use It?

Imagine configuring your entire AWS environment by clicking buttons in the console.
Now imagine doing it again for a second environment (`prod`). Then imagine your
colleague needs to replicate it. And imagine trying to audit what changed six months ago.

**Terraform solves all of this.** You write your AWS resources in code (HCL — HashiCorp
Configuration Language), and Terraform talks to the AWS API to create, update, or
destroy them. Infrastructure becomes:
- **Reproducible:** run the same code, get the same environment.
- **Reviewable:** infrastructure changes go through pull requests and code review.
- **Auditable:** `git log` shows every change ever made.

**We use Terraform for every AWS resource in this project** — S3 buckets, IAM roles,
Lambda functions, Glue jobs, DynamoDB tables, Step Functions, CloudWatch alarms — all
of it.

---

### 5.2 The State File (`terraform.tfstate`) — Why It's Critical

**The Analogy:** The state file is Terraform's **floor plan of what it has already
built**. When you run `terraform apply`, Terraform compares the code you wrote to the
state file to figure out what it needs to add, change, or remove. Without the state
file, Terraform doesn't know what already exists.

**Why it's critical:**
- If the state file is lost, Terraform can't manage the infrastructure it created.
- If the state file is corrupted, Terraform might try to recreate resources that already
  exist — creating duplicates or conflicts.
- The state file can contain **secrets** (database passwords, etc.) — it must be stored
  securely and never committed to Git.

**How we protect it (from `terraform.md`):**
```
State file lives in: s3://ecom-lakehouse-tf-state-{account_id}/env/{dev|prod}/terraform.tfstate
Lock mechanism:       DynamoDB table ecom-lakehouse-tf-locks (prevents two people running
                      terraform apply at the same time — prevents corruption)
Encryption:           S3 bucket with SSE-KMS (state file itself is encrypted)
Access:               Only the gha-deploy-role (GitHub Actions) and admin humans can read it
Never committed:      .gitignore excludes *.tfstate and *.tfstate.backup
```

---

### 5.3 How the Code is Structured

Our Terraform follows a **modules + environments** pattern:

```
infra/
├── modules/              ← reusable building blocks (each does one job)
│   ├── s3_zones/         ← creates all 7 S3 buckets with encryption + policies
│   ├── dynamodb/         ← ledger + watermarks tables
│   ├── iam/              ← all roles and policies
│   ├── glue/             ← Glue jobs + data catalog
│   ├── stepfunctions/    ← state machine + EventBridge rules
│   ├── athena_catalog/   ← workgroup + catalog registration
│   └── observability/    ← CloudWatch alarms + SNS + dashboards
│
└── envs/
    ├── dev/              ← wires modules together with dev-specific values
    │   ├── main.tf       ← calls modules with dev variables
    │   ├── backend.tf    ← points at dev state bucket
    │   └── dev.tfvars    ← dev-specific settings (smaller instances, AWS-managed keys)
    └── prod/             ← same modules, prod-specific values (CMKs, bigger workers)
```

**Modules:** A module is a reusable function for infrastructure. Instead of copy-pasting
the same S3 bucket code 7 times, you write it once in `modules/s3_zones/` and call it
with different parameters.

**Variables:** Inputs to a module. Example: `var.env = "dev"` means the bucket name
becomes `ecom-lakehouse-raw-dev` automatically.

**Outputs:** Values a module exposes to other modules. The `s3_zones` module outputs
`dwh_bucket_arn`, which the `iam` module uses to build the correct policy — they're
linked without hardcoding ARNs.

---

### 5.4 Stateful vs. Stateless Resource Declaration (ADR-018)

**A real footgun to understand:** Terraform's `for_each` loop creates resources
dynamically from a list or map. If you rename a key in that map, Terraform plans to
**destroy and recreate** that resource. For a DynamoDB table or S3 bucket containing
production data, this would delete the data.

**Our fix:** Stateful resources (`raw`, `dwh`, `archive`, `quarantine` buckets,
DynamoDB tables) are declared as **explicit individual resource blocks** with
`lifecycle { prevent_destroy = true }`. This makes `terraform destroy` (or a careless
rename) error out rather than deleting the bucket.

Stateless resources (`staging`, `athena-results`, `artifacts` — destroy/recreate is
harmless) can safely use `for_each`.

---

### 5.5 The Five Terraform Guardrails (terrashark-aligned)

When writing or reviewing Terraform code for this project, check these five failure modes:

1. **Identity churn** — review every `plan` for unexpected `-/+` (destroy+create) on named resources. A renamed variable shouldn't delete a production bucket.
2. **Secret exposure** — run `detect-secrets` and `tfsec` in CI. Mark sensitive outputs `sensitive = true`. Never log the state file.
3. **Blast radius** — `prevent_destroy = true` on every stateful bucket/table. Separate state files for dev and prod so a bad prod apply can't affect dev.
4. **CI drift** — a scheduled `terraform plan` in GitHub Actions detects if someone manually changed something in the AWS console (drift). It fails the pipeline if the plan is non-empty.
5. **Compliance gates** — `checkov` and `tflint` check every PR to ensure encryption, versioning, and public-access-block are present on all buckets.

---

## SECTION 6 — Interview & Review Prep: Q&A

These are questions a technical reviewer would realistically ask a junior engineer about
this architecture. Practice saying the answers out loud.

---

### Q1: "You have seven S3 buckets. Isn't that overkill? Why not use one bucket with prefixes?"

**Structured Answer:**

"The separation isn't cosmetic — it's a security and operational boundary. Each bucket
gets its own IAM resource-level permissions, its own lifecycle policy, and its own
encryption key in production.

For example, the `raw` bucket is immutable and versioned — no Lambda or Glue job can
delete from it, only S3 or a specific archive Lambda. The `dwh` bucket has Delta tables
and is protected with `prevent_destroy` in Terraform. The `staging` bucket auto-expires
in 7 days and doesn't need long-term retention.

If we used one bucket with prefixes, we'd have to write complex IAM conditions to
enforce these boundaries, and a misconfigured policy could accidentally give a Lambda
write access to the warehouse. With separate buckets, the blast radius of a mistake is
contained.

We also apply different Deny policies per bucket. Each data bucket has `EnforceHTTPS`
and `EnforceKMSEncryption` bucket policies — this ensures that even if a client
explicitly tries to upload without KMS, the bucket rejects it. That's hard to enforce
cleanly across prefixes in a single bucket."

---

### Q2: "Why did you choose Step Functions Standard instead of Express Workflows? Express is cheaper."

**Structured Answer:**

"Express Workflows have a hard 5-minute timeout. Our Glue Spark jobs take 2–4 minutes
just to start the cluster — the 5-minute limit would fire before the job finishes, and
it would silently pass without completing the work.

More critically, Express Workflows use at-least-once semantics — a step can execute
twice if there's a transient failure. For a pipeline writing to a financial data
warehouse, duplicating records would be a serious correctness bug.

Standard Workflows guarantee exactly-once execution and support up to one-year
executions. They also keep full execution history in the console, which is invaluable
when debugging a failure.

On cost: at our volume — roughly monthly runs with about 10 state transitions each —
Standard costs approximately $0.15 per month. Express would be around $0.003. That $0.14
difference doesn't justify the correctness risk or the timeout limitation."

---

### Q3: "Why are your Delta tables unpartitioned? The brief says 'Partitioning for performance.' Did you miss this?"

**Structured Answer:**

"No — and this is actually a more sophisticated answer than partitioning reflexively
would have been.

Delta Lake documentation recommends avoiding physical partitioning for tables under 1 TB.
Our monthly drop is ~500 orders and ~2,768 items — roughly 1 MB of data. Every order in
a monthly file shares the same `order_date`, so a daily partition would hold one month
of data in a handful of KB-sized Parquet files. S3 charges per GET request, and each
tiny file is a separate request. We'd pay more to scan less data.

Instead, we use Delta's Z-ORDER compaction: `OPTIMIZE fct_orders ZORDER BY (order_date)`.
This clusters rows by `order_date` within each file and stores min/max statistics per
file. When Athena queries `WHERE order_date = '2025-04-01'`, it checks those statistics
and skips files that can't contain that date — effectively the same pruning benefit as
partitioning, without the small-file penalty.

We've documented a promotion threshold: when a single date's data grows to ≥ 1 GB,
we'll switch to physical `order_date` partitioning. The partition key, merge key, and
schema don't change — it's a one-time rewrite.

So the brief's requirement for 'Partitioning for performance' is satisfied by a
deliberate, justified decision with a documented upgrade path — not by blindly applying
partitioning to a table with 500 rows."

---

### Q4: "What happens if someone runs the pipeline twice on the same file?"

**Structured Answer:**

"Nothing changes in the warehouse — that's the idempotency guarantee. We have two
independent layers protecting against this.

Layer 1: The `ClaimFile` Lambda writes to DynamoDB with a conditional expression —
it only succeeds if the `file_key` doesn't exist, or if its status is `FAILED`. If the
file was already processed and archived, the conditional write raises a
`ConditionalCheckFailedException`, Step Functions catches it and routes to
`NoOpSucceed`. The pipeline exits cleanly without running any Glue jobs.

Layer 2: If the DynamoDB check somehow passed (for example, we're re-processing to fix
a bug in a previous load), the Delta MERGE still protects us. MERGE compares incoming
rows to existing rows by the natural key (`order_id`). If the row already exists and
the `_record_hash` is identical, the MERGE skips it — no write occurs. If the hash
differs (we fixed something), the row is updated in place.

So idempotency is both at the file level via DynamoDB and at the row level via Delta
MERGE. Re-running a job is always safe."

---

### Q5: "Walk me through your security model. If the Glue job were compromised, what damage could it do?"

**Structured Answer:**

"The Glue job runs as `glue-ingest-role`. Let me walk through exactly what that role
can and can't do.

It can: read from the staging bucket, read and write to the DWH bucket, write rejected
records to the quarantine bucket, and read/write the DynamoDB ledger and watermark tables.

It cannot: read from or write to the raw bucket — so it can't touch the immutable
originals. It cannot write to the archive bucket — so it can't fake an archival. It
cannot write to the artifacts bucket — so it can't replace Glue scripts with malicious
code. It cannot call IAM APIs, interact with other accounts, or spin up EC2 instances.

The IAM policies are resource-scoped to specific bucket ARNs with environment suffixes —
a dev Glue job can't access prod buckets even if the role name is similar.

On top of IAM, every S3 PUT must use KMS encryption — the `EnforceKMSEncryption` bucket
policy blocks any upload with the `AES256` header, returning a 403. This means even a
compromised Glue job can't write data in a way that bypasses KMS audit.

CloudTrail records every API call. If the Glue job called S3 `DeleteBucket` or
`PutBucketPolicy`, the call would fail (not in the role) and the attempt would be logged,
triggering a CloudWatch alarm."

---

## Quick Reference Glossary

| Term | Plain English |
|------|--------------|
| **ADR** | Architecture Decision Record — a documented "we chose X because Y, and we considered Z" |
| **ACID** | Atomic, Consistent, Isolated, Durable — guarantees that database writes either fully succeed or fully fail |
| **Idempotency** | Running an operation multiple times produces the same result as running it once |
| **Medallion architecture** | Bronze (raw) → Silver (clean) → Gold (aggregated) — layers of data quality |
| **MERGE / upsert** | Insert if new, update if exists — avoids duplicates on re-processing |
| **Z-ORDER** | Delta table optimization that physically clusters similar data rows together for faster scans |
| **VACUUM** | Delta operation that removes old Parquet files no longer referenced by the transaction log |
| **Batch ID** | Unique identifier (`orders-20250401-abc123`) stamped on every record, file path, and log entry for full traceability |
| **DPU** | Data Processing Unit — Glue's billing unit (1 DPU = 4 vCPU + 16 GB RAM) |
| **OIDC** | OpenID Connect — a protocol that lets GitHub prove its identity to AWS without storing access keys |
| **State file** | Terraform's record of what infrastructure it has already created |
| **Quarantine** | Where rows go when they fail validation — kept 180 days for inspection and replay |
| **Conditional write** | A DynamoDB write that only succeeds if a condition is true — prevents race conditions |
