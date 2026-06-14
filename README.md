# ecom-lakehouse

A production-grade data lakehouse built on AWS for processing e-commerce transactions at
scale. The system ingests raw order and product data, normalizes it, enforces data
quality contracts, and makes it available for analytical queries — all automatically,
reliably, and with the operational guarantees that production data engineering requires.

---

## What This Is

E-commerce platforms generate a continuous stream of transactional data: products,
orders, and the line items that link them. This data arrives in different formats (CSV,
Excel), at irregular times, and with no guarantees of cleanliness. Someone has to receive
it, normalize it, validate it, store it in a queryable form, and make sure that running
the same pipeline twice does not double-count anything.

This project is a complete implementation of that system on AWS, using the medallion
lakehouse pattern — a layered architecture where data moves from raw to clean in clearly
defined, independently recoverable stages.

The design targets several properties that matter in production:

**Reliability over performance.** ACID writes via Delta Lake mean a failed job does not
leave partially-written data. The pipeline can be re-run at any point and will converge
to the correct state.

**Exactly-once processing.** A DynamoDB control plane tracks every file's lifecycle.
If the same file lands in S3 twice, it is processed once. If a Glue job crashes mid-run
and is retried, the MERGE operation produces the same result as a clean run.

**Separation of concerns.** Each stage of the pipeline has a single responsibility and
its own compute, IAM role, storage zone, and failure mode. A normalization failure does
not affect a dataset that was already normalized. A bad record does not abort a batch
that is otherwise clean.

**Auditability.** Every file that arrives is preserved in raw form indefinitely. Every
rejected record lands in a quarantine zone with the reason it was rejected. The Delta
transaction log provides a full history of every table write. DynamoDB records row
counts at each stage.

---

## The Data Pipeline

```
Raw S3
  │  (xlsx / csv land here, immutable, versioned)
  │
  ▼
Lambda Normalize
  │  pandas + openpyxl convert xlsx→Parquet, enforce canonical column names
  │
  ▼
Staging S3
  │  (clean Parquet, 7-day expiry)
  │
  ▼
AWS Glue + Spark
  │  validate records, compute SHA-256 dedup hash, Delta MERGE on natural keys
  ├──▶  DWH S3 (Delta tables, ACID, queryable via Athena)
  └──▶  Quarantine S3 (rejected records + reject_reason)
  │
  ▼
Archive S3
  │  (original raw files moved here after successful load)
  │
  ▼
Glue Data Catalog + Athena
     (native Delta tables, no manifests, no MSCK REPAIR)
```

The entire lifecycle is coordinated by AWS Step Functions. A Standard workflow handles
retry logic, branching on failure, alerting via SNS, and the sequential dependencies
between normalization and ingestion.

---

## Three Datasets

**dim_products**: A product dimension table. 1,000 products across 6 departments (Books,
Sports, Toys, Home, Clothing, Electronics). Each product has a `product_id`, a
`department_id`, a `department` name, and a `product_name`. This is the reference table
that order items link to.

**fct_orders**: Order-level fact table. Each row represents one customer order with a
unique `order_id`, a `user_id`, a timestamp, and a total amount. Source files arrive as
Excel.

**fct_order_items**: Line-item fact table. Each row is one product within one order,
linked by `order_id` and `product_id`. This is the most granular table and the largest
by row count. Source files arrive as Excel.

Validation rules are enforced at ingest time: no null primary keys, valid timestamps, and
referential integrity between order items, orders, and products. Records that fail
validation are quarantined, not silently dropped or allowed to corrupt the table.

---

## Architecture Decisions Worth Knowing

Every significant choice in this project was made deliberately and is documented with
rationale. A few of the non-obvious ones:

**Lambda for normalization, not Glue Python-shell.** Glue Python-shell costs a minimum
of one DPU-minute per invocation (~$0.44/DPU-hour). A Lambda function that converts a
1 MB Excel file to Parquet takes under a second and costs a fraction of a cent. Lambda is
the right tool for fast, stateless format conversion; Glue + Spark handles the heavy
distributed transformation work.

**One Glue job per dataset, not one job for all three.** Per-dataset jobs give granular
failure isolation and independent retry — a bad orders file does not prevent products from
loading, and each dataset can be reprocessed without touching the others.

**Tables are unpartitioned.** At the current volume, physical date partitioning creates
KB-scale partitions — smaller than the Delta log overhead. Z-Ordering on `order_date`
gives partition pruning benefits without fragmentation. Physical partitioning is
introduced when a single partition would hold meaningful data (threshold: ~1 GB).

**Athena reads Delta natively.** No symlink manifests, no `MSCK REPAIR TABLE`. Amazon
Athena v3 reads the Delta transaction log directly. Catalog tables are registered with
`table_type=DELTA` via Terraform-managed DDL.

**No Glacier for archive.** Per-object overhead and transition fees exceed storage
savings for files this size. S3 Standard is both cheaper and simpler.

The full decision log with context, rejected alternatives, and consequences is in
`docs/decision.md`. The architecture source of truth — zone definitions, schemas, naming
conventions — is in `docs/architecture.md`.

---

## Repository Layout

```
ecom-lakehouse/
├── src/
│   ├── lakehouse/          # Reusable Spark library (schemas, validation, transforms, merge)
│   ├── glue_jobs/          # Glue entrypoints — thin wrappers calling the library
│   ├── normalize/          # Lambda normalizer (xlsx/csv → Parquet via pandas/openpyxl)
│   ├── lambdas/            # Helper Lambdas: claim batch, archive file, validate schema
│   ├── athena/             # Post-load validation SQL queries
│   └── ui/                 # Streamlit dashboard for local inspection
├── infra/
│   ├── modules/            # Terraform modules: s3_zones, iam, glue, dynamodb, lambda,
│   │                       #   stepfunctions, observability
│   └── envs/dev/           # Dev environment: wires modules together, tfvars, backend
├── orchestration/          # Step Functions ASL definition (the state machine JSON)
├── tests/
│   ├── unit/               # Spark library tests, no AWS
│   ├── integration/        # End-to-end job tests, local Delta
│   └── fixtures/           # Sample data including intentionally dirty records
├── scripts/
│   ├── smoke_test.py       # Post-deploy infrastructure validation
│   └── set_aws_profile.ps1 # Load sandbox credentials into AWS profile
├── .github/workflows/
│   ├── ci.yml              # Lint, security scan, tests, terraform validate
│   └── deploy.yml          # Terraform apply + artifact upload + smoke test (main only)
├── docs/                   # 21 planning documents covering every subsystem
└── .ai/
    ├── architecture_decisions.md  # Deep explanation of every design decision
    ├── how_to_test.md             # Testing guide from unit tests to end-to-end
    ├── architecture_guide.md      # Interview and study reference
    └── status.md                  # Build status, sprint log, cost tracking
```

---

## Infrastructure Overview

Running `terraform apply` in `infra/envs/dev` provisions:

- 7 S3 buckets with KMS encryption, bucket policies (HTTPS + KMS enforcement), versioning,
  and lifecycle rules
- 2 DynamoDB tables (ingestion ledger + watermarks)
- 3 Lambda functions with their IAM roles
- 1 Glue job (parameterized per dataset) with its IAM role
- 1 Glue Data Catalog database with 3 native Delta table definitions
- 1 Step Functions Standard state machine
- 1 Athena workgroup
- 1 SNS topic + email alert subscription
- CloudWatch log groups, a dashboard, and a metric alarm for pipeline failures
- An EventBridge rule that fires on Glue job failures
- IAM roles and policies for every component
- GitHub OIDC trust (enabling keyless AWS authentication from GitHub Actions)

---

## CI/CD

Every push to any branch runs the CI workflow: lint (black, isort, flake8), secret
scanning (detect-secrets), unit tests, integration tests, and terraform validate.

Every push to `main` additionally runs the deploy workflow: terraform apply, build and
upload the lakehouse wheel and Glue scripts to S3, and a smoke test that verifies the
deployed infrastructure is reachable.

Authentication uses GitHub OIDC — no static AWS keys are stored anywhere. The deploy role
trust policy is scoped to `ref:refs/heads/main` on this specific repository.

---

## Development

```bash
# Install all dependencies including dev tooling
pip install -e ".[dev]"

# Run tests
make test

# Run linters
make lint

# Terraform plan (no apply)
make plan
```

For detailed testing instructions — including how to run a manual end-to-end pipeline,
check the DynamoDB ledger, query Athena, and verify quarantine behavior — see
`.ai/how_to_test.md`.

---

## Cost

Estimated cost per full deploy-test-destroy cycle on the dev environment: under $1.00.

Always run `terraform destroy` after smoke testing. The sandbox account has a limited
budget. The Terraform state bucket and DynamoDB lock table (manually bootstrapped, not
managed by Terraform) should be deleted manually when the account is decommissioned.
