# Architecture Decisions & Design Rationale

This document explains every significant decision made in the ecom-lakehouse project —
why it was made, what was considered and rejected, and what the downstream consequences
are. It is meant to give a complete picture for anyone who needs to understand the system
deeply, not just operate it.

---

## The Original Brief — What Was Asked For

The project brief asked for a production-grade Lakehouse on AWS for an e-commerce
platform. The core mandate was:

- Raw transactional files land in S3.
- They get cleaned and deduplicated with Delta Lake.
- Cleaned data is queryable in Amazon Athena.
- The whole pipeline is orchestrated by AWS Step Functions.
- Deployment is automated via GitHub Actions.
- Mandatory services: S3, Glue + Spark, Delta Lake, Step Functions, Glue Data Catalog,
  Athena, GitHub Actions.

Three datasets were provided: `products.csv` (1,000 rows), `orders_apr_2025.xlsx`
(500 rows), and `order_items_apr_2025.xlsx` (2,768 rows). The scale is intentionally
small — the architecture must be designed for production correctness, not current volume.

---

## Overall Architecture — The Medallion Pattern

The system uses a medallion architecture, which is a layered data storage model where
data moves from raw to progressively cleaner, more structured zones. Each zone has a
clear responsibility and a defined format.

**Raw zone** (`ecom-lakehouse-raw-dev`): The immutable landing zone. Files land here
exactly as the upstream system sends them — `.xlsx`, `.csv`, or any future format. Nothing
is ever deleted from raw; it is the system of record for "what we received." S3 versioning
is enabled so even overwrites are recoverable.

**Staging zone** (`ecom-lakehouse-staging-dev`): An ephemeral transit zone. Lambda reads
the raw file, normalizes it to Parquet with canonical column names and types, and writes
it here. This zone has a 7-day lifecycle expiry — staging files are throwaway; the value
is in the raw original and the final Delta table, not the intermediate Parquet.

**DWH zone** (`ecom-lakehouse-dwh-dev`): The curated Delta Lake warehouse. All three
datasets live here as ACID-compliant Delta tables: `dim_products`, `fct_orders`,
`fct_order_items`. This is where Athena reads from.

**Archive zone** (`ecom-lakehouse-archive-dev`): After a raw file is successfully
processed, the Lambda archive function copies it here under a `/<dataset>/<batch_id>/`
key. This preserves the file with its original format and marks the batch as complete.

**Quarantine zone** (`ecom-lakehouse-quarantine-dev`): Any records that fail validation
(null primary keys, bad timestamps, referential integrity failures) land here with a
`reject_reason` column. The run does not fail because of these records; the valid subset
still loads.

**Athena Results** (`ecom-lakehouse-athena-results-dev`): Required by Athena to write
query output. 30-day lifecycle expiry.

**Artifacts** (`ecom-lakehouse-artifacts-dev`): Holds the deployed Glue job scripts
(`.py`) and the packaged `lakehouse` Python wheel (`.whl`) that is distributed to Glue
workers as a dependency.

---

## The Normalization Step — Why Lambda Fronts the Glue Jobs

The brief says the data comes from CSVs, but the actual sample files are Excel (`.xlsx`).
Spark/Glue cannot read `.xlsx` natively. There is a `spark-excel` third-party JAR, but it
is fragile, version-coupled to the Spark runtime, and difficult to test reliably.

The solution was to put a lightweight normalization step in front of every Glue job. This
step reads the raw file using `pandas` and `openpyxl` (which handle Excel perfectly),
applies the canonical column names and data types defined in the Design Contract, and
writes a clean Parquet file to the staging zone. Glue then reads Parquet, which it handles
natively and efficiently.

The key question was: what compute should run this normalization? Two options were
considered.

A Glue Python-shell job was the original plan. It costs at minimum 1 DPU-minute per run,
billed at ~$0.44/DPU-hour. For a file that takes under a second to process, this is pure
waste.

Lambda was chosen after reviewing this. A Lambda function invoking `pandas` via a layer
or container image costs microseconds and a fraction of a cent per invocation. The cold
start is milliseconds. It is roughly 95% cheaper than Glue Python-shell for this workload.
More importantly, the brief does not constrain the normalization runtime — it says "AWS
Glue + Spark for ETL," and ETL means the heavy transformation work, not file format
conversion.

This is recorded as ADR-002 and ADR-011.

---

## Per-Dataset Glue Jobs — Why There Are Three, Not One

An architectural review suggested consolidating all three datasets into a single Glue
Spark session. This was explicitly rejected.

The brief states: "Run a Glue Job with Delta Lake for each dataset." This is a direct
requirement. Consolidating to one session violates it.

Beyond compliance, per-dataset jobs have operational value. If the `orders` job fails,
the `products` and `order_items` jobs are unaffected and can retry independently. The Step
Functions state machine branches per dataset, so failure handling is granular. A single
session creates a shared failure domain where one dataset's issue aborts all three.

The per-dataset Glue job IS parameterized — there is one job definition with a `dataset`
parameter. The Step Functions state machine invokes it three times with different
parameters. This avoids code duplication while respecting the per-dataset requirement.

Single-session consolidation is documented in `docs/glue_jobs.md` as an optional
optimization with its tradeoffs — it is not removed from the conversation, just not the
default.

---

## Delta Lake — Why It Was Chosen and What It Gives

The brief mandates ACID, schema enforcement, deduplication, and upserts. Delta Lake
satisfies all four:

**ACID transactions**: Writes are all-or-nothing. A Glue job that crashes mid-write does
not leave a partially-written table; the transaction log records uncommitted writes as
non-existent.

**MERGE/upsert**: The core deduplication mechanism. The MERGE operation matches incoming
rows against existing rows on the natural key (`product_id` for products, `order_id` for
orders, the `(order_id, product_id)` composite for order items). If a match exists, it
updates; if not, it inserts. This means re-running the same pipeline on the same file
produces no duplicates.

**Schema enforcement**: Delta rejects writes that violate the registered schema. If a
new file adds a column that does not exist in the schema, the write fails. Schema changes
must go through a migration — there is no auto-evolution in production.

**Time travel**: Every write creates a new version in the Delta transaction log. You can
query the table as it existed at any prior version. Useful for debugging and auditing.

Alternatives considered: Apache Iceberg (also strong, comparable features) and Apache
Hudi (more operational overhead, less mature Glue integration at the time of design).
Plain Parquet was rejected because it provides none of the ACID/MERGE guarantees.

---

## Deduplication Strategy — The `_record_hash` and MERGE Keys

The brief requires deduplication across files. The strategy uses two layers:

**Within a batch**: Before writing, a SHA-256 hash is computed over all business columns
of each row. This `_record_hash` column is stored with the row. Within a single
incoming file, rows with duplicate hashes are dropped — only one is kept per natural key.

**Across batches**: The MERGE operation on the Delta table handles this. A row whose
natural key already exists in the table is updated (not inserted again). A row whose
natural key is new is inserted. This means loading the same file twice produces the same
result as loading it once.

**DynamoDB ledger for file-level idempotency**: MERGE handles row-level idempotency.
But there is a higher-level concern: what if the same raw file is dropped into S3 twice?
The DynamoDB ingestion ledger addresses this. Before processing any file, the Step
Functions workflow checks the ledger for this batch ID. If the batch is already in
`LOADED` status, the workflow skips it without re-running Glue. This is the "exactly-once
effect" guarantee — even if a file is received multiple times, it is processed at most once.

---

## Partitioning Decision — Why the Tables Are Not Partitioned

The brief explicitly lists "Partitioning for performance" as a deliverable, which might
suggest the tables should be partitioned. They are not, and this was a deliberate
decision that demonstrates more engineering judgment than reflexively partitioning.

At the current volume — ~500 orders per monthly file, all sharing the same `order_date` —
physical date partitioning would create partitions holding roughly one month of data each.
That is ~12 tiny partitions per year, each containing KB-scale files. The overhead of
Delta transaction log management and S3 list operations for these tiny files exceeds the
query performance benefit partitioning is supposed to provide. This is the "small file
problem."

Instead, the tables use **Z-Ordering**. Z-Ordering co-locates rows with similar values in
the same files without creating separate directories. For `fct_orders` and
`fct_order_items`, Z-Ordering is applied on `order_date`. For `dim_products`, on
`department`. Athena's data skipping reads the min/max statistics in the Delta log and
skips files that cannot contain matching rows — this gives most of the partition pruning
benefit without file fragmentation.

The design includes a documented promotion threshold: once any single date's data would
fill a meaningful partition (rule of thumb: ≥1 GB, or a sustained daily order cadence
rather than monthly drops), physical `order_date` partitioning is introduced. This is the
correct engineering answer — not "never partition" but "partition when it helps."

---

## Step Functions Standard — Not Express

AWS Step Functions has two workflow types: Standard and Express.

Express workflows are designed for high-throughput, short-duration events. They have a
maximum execution duration of 5 minutes and use at-least-once semantics.

Glue jobs run for several minutes. The pipeline includes multiple sequential steps with
retry logic. Standard workflows support executions lasting up to one year, have
exactly-once semantics, and maintain a full execution history in the console. The per-
transition pricing is higher than Express, but at a monthly batch cadence, the total cost
is negligible.

Express was not a viable option because of the 5-minute timeout and at-least-once
semantics. An at-least-once orchestrator running a MERGE job is dangerous — it could
trigger duplicate Glue runs while the first is still in flight. Standard's exactly-once
semantics, combined with the DynamoDB ledger, gives clean idempotency guarantees.

---

## Athena Native Delta — No Symlink Manifests

Older versions of Delta Lake on AWS required generating symlink manifest files (via
`delta.GENERATE symlink_format_manifest`) and running `MSCK REPAIR TABLE` to make Athena
aware of new partitions. This is fragile and adds write overhead.

Amazon Athena v3 (which this project pins) reads Delta tables natively using
`table_type=DELTA` in the Glue Data Catalog. Athena reads the Delta transaction log
directly — it is always current without any manifest generation or catalog repair. This
approach is simpler, cheaper, and more reliable.

The catalog table is registered via Terraform-managed DDL (`CREATE TABLE` with
`table_type=DELTA`), not via a Glue Crawler. Crawlers can mis-infer types and introduce
latency; explicit DDL is version-controlled and deterministic.

---

## DynamoDB Control Plane — Ledger and Watermarks Only

DynamoDB was added to the design beyond what the brief strictly required. The reason is
that S3 alone cannot provide a cheap, strongly-consistent, conditional-write record of
"have we already processed this file?"

The DynamoDB control plane has two tables:

**Ingestion ledger** (`ecom_lakehouse_ingestion_ledger_dev`): One row per batch,
recording the batch ID, dataset, status (`CLAIMED`, `NORMALIZED`, `LOADED`, `ARCHIVED`,
`FAILED`), row counts at each stage, and the reject count. DynamoDB's conditional writes
ensure that two concurrent Lambda invocations for the same file cannot both claim it —
only one will win the conditional write.

**Watermarks** (`ecom_lakehouse_watermarks_dev`): Records the last successfully processed
timestamp or file key per dataset. Used to avoid re-processing old files and to drive
incremental loads.

An earlier version of the design also included a DynamoDB schema registry — a table that
stored expected schemas for each dataset. This was removed. Delta Lake already enforces
schemas on write. The normalizer validates incoming structure against the in-code schema
definition before Spark ever runs. The registry's only additional value would have been
storing schema versions without requiring a code deploy, but the design deliberately avoids
auto-evolution — all schema changes must go through code review and a migration. The
registry was solving a problem that does not exist in this design.

---

## IAM Design — Least Privilege and Role Separation

Each compute component in the pipeline has its own IAM role with only the permissions it
needs:

**GHA Deploy Role**: The role assumed by GitHub Actions via OIDC. It has
`AdministratorAccess` during the current sprint (infrastructure provisioning phase), with
a planned scope-down to Terraform-specific permissions in production hardening. The OIDC
trust policy restricts it to commits on the `main` branch of this specific repository — no
wildcards on branch or repo.

**Glue Ingest Role**: Can read from `staging`, write to `dwh` and `quarantine`, and
read/write DynamoDB for the ledger and watermarks. It cannot read from `raw` directly —
that is Lambda's responsibility. It cannot delete data from any zone.

**Lambda Roles**: The normalize Lambda reads from `raw`, writes to `staging`, and writes
to the DynamoDB ledger. The archive Lambda reads from `raw`, writes to `archive`, and
updates the ledger. Each Lambda role is scoped to its specific action.

All S3 bucket policies enforce two denies regardless of the caller's IAM role:

**EnforceHTTPS**: Denies any request where `aws:SecureTransport` is false. All traffic
must be encrypted in transit.

**EnforceKMSEncryption**: Denies any `PutObject` request that does not include the
`x-amz-server-side-encryption: aws:kms` header. This means callers must explicitly
request KMS encryption; the bucket's default encryption alone is insufficient to satisfy
the policy. This is why all `aws s3 cp` commands and `put_object` calls in the pipeline
explicitly pass `--sse aws:kms`.

---

## GitHub Actions OIDC — No Long-Lived AWS Keys

The CI/CD pipeline authenticates to AWS using GitHub's OIDC provider, not static
`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` credentials. When the workflow runs, GitHub
issues a short-lived JWT token. The `aws-actions/configure-aws-credentials` action
presents this token to AWS STS to assume the GHA Deploy Role. The resulting credentials
last for the duration of the workflow and are automatically invalidated.

The OIDC trust policy uses `StringEquals` (not `StringLike`) on the `sub` claim and
restricts the allowed value to exactly `repo:eli-bigman/project-2-lakehouse:ref:refs/heads/main`.
This means:

- Only the `eli-bigman/project-2-lakehouse` repository can assume this role.
- Only workflows triggered by a push to `main` can assume it — not PRs, not other
  branches, not forks.
- The `environment: dev` block was deliberately removed from the deploy workflow, because
  adding it changes the OIDC `sub` claim to `environment:dev` and would require a second
  trust policy entry — a broader surface for no operational benefit.

---

## Glue Worker Configuration — Fixed 2 Workers, No Auto-Scaling

Glue auto-scaling sounds attractive — spin up more workers when the job needs them. But
it has two problems for this workload:

First, the AWS API minimum for a Glue Spark job is 2 workers. Setting 1 worker is an API
error, not just a misconfiguration.

Second, auto-scaling has a startup delay. If the job finishes quickly (which it will at
this data volume), auto-scaling may never have time to react — the job completes before
additional workers are provisioned. The result is a longer-than-necessary run due to the
scaling wait, not a shorter one.

Fixed configuration of 2 `G.1X` workers (4 vCPU, 16 GB RAM, 64 GB disk each) is more
than sufficient for 2,768 order items. The consistent allocation means predictable billing
and no scaling-related delays.

---

## DynamicFrames Prohibited — Spark DataFrames Only

AWS Glue has a proprietary data structure called a `DynamicFrame`. It is designed for
the Glue Data Catalog and Glue's own transformation APIs. It does not support Delta Lake.
If you try to read a Delta table using `create_dynamic_frame.from_catalog` or write one
using `DynamicFrame`, Glue will either error or silently bypass the Delta transaction log
and read/write plain Parquet files — meaning all ACID guarantees are lost without any
obvious error.

All reads and writes in the Glue jobs use native Spark DataFrames:
`spark.read.format("delta").load(path)` for reads and `DeltaTable.forPath(spark, path)`
for MERGE operations. This is not optional — the DynamicFrame prohibition is enforced by
convention and documented so that anyone extending the jobs does not accidentally
introduce it.

---

## Stateful Buckets — Explicit Terraform Resources, Not for_each

Terraform's `for_each` is convenient for iterating over a map of similar resources. But
it has a dangerous property: if you rename a key in the map, Terraform destroys the old
resource and creates a new one. For S3 buckets holding the raw, archive, DWH, and
quarantine data, this is catastrophic.

The four stateful buckets are declared as individual `resource` blocks with
`lifecycle { prevent_destroy = true }`. Terraform will refuse to destroy them even if
`terraform destroy` is run, unless `prevent_destroy` is explicitly set to `false` in the
variable. This is the expected behavior — production data stores should require explicit
override to delete.

The three stateless buckets (`staging`, `athena-results`, `artifacts`) use `for_each`
because losing them is safe: staging is ephemeral, Athena results are re-queryable, and
artifacts are regenerated by CI/CD.

---

## KMS Encryption Strategy — Bucket Keys and Dev vs. Prod

All S3 buckets use `aws:kms` server-side encryption. The specific key depends on the
environment:

**Dev**: Uses the AWS-managed key (`alias/aws/s3`). AWS manages the key automatically
with no fixed cost. Suitable for development where the main concern is demonstrating the
correct architecture, not incurring ~$1/key/month fixed KMS charges.

**Prod**: Uses customer-managed KMS keys (CMKs) — one per data class. CMKs allow
defining granular key policies (who can use the key, who can administer it), enabling
automatic rotation, and providing a detailed audit trail in CloudTrail. For a
production-grade system, CMKs are the correct choice despite the fixed cost.

Both environments enable **S3 Bucket Keys** (`bucket_key_enabled = true`). Without
Bucket Keys, every single S3 PUT/GET generates a KMS API call. With Bucket Keys, S3
caches the data key at the bucket level and uses it for multiple objects, reducing KMS API
calls by approximately 99%. This was previously the main cost argument against CMKs — the
Bucket Keys feature removes that argument.

---

## Storage Tiering — No Glacier

Glacier is S3's archival storage class with very low per-GB pricing but per-object
metadata overhead and per-transition request fees. For large objects stored for years,
Glacier can be dramatically cheaper than S3 Standard.

For this project's files — typically ~1 MB each, processed monthly — Glacier is
counterproductive. The per-object overhead (8 KB minimum storage charge per object in
Glacier Deep Archive) and the transition request fees exceed the storage savings for small
files. A 1 MB file that costs $0.000023/month in S3 Standard costs more to transition
to Glacier than to just leave it in Standard.

The archive zone uses S3 Standard with an optional upgrade to Intelligent-Tiering if
access patterns become unpredictable. If file volume grows to the point where objects are
genuinely large and cold (>10 MB, accessed rarely), Glacier becomes worth evaluating.

---

## Sandbox Constraints — Region Restriction

This project was deployed in an Amalitech DCE (Disposable Cloud Environment) — a sandbox
AWS account (`970547336735`) with a policy (`DCEPrincipalDefaultPolicy-dce`) that
restricts all resource creation to `eu-central-1` and `eu-west-1`. Attempts to create
resources in `us-east-1` fail with an explicit `AccessDenied`.

The project design specified `us-east-1` (ADR-009), but the actual deployment uses
`eu-west-1`. All ARN references, backend configurations, provider blocks, and environment
variables were updated accordingly. This is purely an infrastructure deployment detail —
the architecture is region-agnostic, and the region decision in ADR-009 would stand for
an unrestricted account.

---

## What Was Deployed to AWS

Running `terraform apply` in this project provisions the following:

- 7 S3 buckets (raw, staging, dwh, archive, quarantine, athena-results, artifacts) with
  KMS encryption, versioning, bucket policies (EnforceHTTPS + EnforceKMSEncryption), and
  lifecycle rules.
- 2 DynamoDB tables (ingestion ledger + watermarks) with SSE-KMS.
- 3 Lambda functions (normalize, archive, claim) with their IAM roles.
- 1 Glue job (parameterized; one invocation per dataset) with its IAM role.
- 1 Glue Data Catalog database and 3 native Delta table definitions.
- 1 Step Functions Standard state machine with the full pipeline ASL.
- 1 Athena workgroup with query result encryption and cost controls.
- 1 SNS topic + email subscription for pipeline failure alerts.
- 1 EventBridge rule (Glue job state change → SNS on FAILED/TIMEOUT/ERROR).
- 1 CloudWatch log group + CloudWatch dashboard.
- 1 CloudWatch metric alarm (Step Functions execution failures).
- IAM roles and inline/managed policies for each compute component.
- An OIDC provider trust relationship (pre-existing in the sandbox) enabling GitHub
  Actions to assume the GHA Deploy Role without static credentials.
- Terraform remote state in a separately-bootstrapped S3 bucket with DynamoDB locking.

---

## Testing Strategy

**Unit tests** (`tests/unit/`): Test the Spark library functions in isolation using a
local SparkSession (no AWS credentials required). Test schema validation, MERGE key
generation, hash computation, and validation rule application. PySpark lazy evaluation
requires that all `F.col()` expressions inside validation rule predicates are wrapped in
zero-argument lambdas — they must not be evaluated at module import time, only when a
SparkSession is active.

**Integration tests** (`tests/integration/`): Test Glue job behavior end-to-end against
a local Delta warehouse using `pyspark.sql.SparkSession` with Delta configured. No
mocking of Spark or Delta.

**CI pipeline**: black + isort (formatting), flake8 (lint), detect-secrets (secret
scanning), pytest unit + integration tests, terraform validate. All must pass before
merge to main.

**Smoke test** (`scripts/smoke_test.py`): Post-deploy infrastructure validation. Uploads
a canary CSV to the raw bucket (with `ServerSideEncryption='aws:kms'` to satisfy the
bucket policy), starts a Step Functions execution, and polls for a terminal state. At this
sprint stage, the placeholder Lambda and Glue code will cause the execution to FAIL — but
that proves the infrastructure (S3, Step Functions, IAM trust) is operational and
reachable. The smoke test treats FAILED/TIMED_OUT executions as `INFRA_OK`.
