# ecom-lakehouse

Production-grade medallion Lakehouse on AWS for e-commerce transactions.

## Architecture

```
Raw S3 → Lambda (normalize xlsx/csv→Parquet) → Glue Spark + Delta Lake (validate/MERGE)
       → Glue Data Catalog → Amazon Athena (analytics)
       Orchestrated by AWS Step Functions | Deployed via GitHub Actions CI/CD
```

**Zones:** `raw` → `staging` → `dwh` (Delta) | `archive` | `quarantine`  
**Datasets:** `dim_products`, `fct_orders`, `fct_order_items`  
**Control plane:** DynamoDB ingestion ledger + watermarks

## Quick Start

```bash
# 1. Prerequisites: AWS CLI v2, Terraform >= 1.6, Python 3.11+
cp .env.example .env        # fill in AWS_ACCOUNT_ID, ALERT_EMAIL, etc.

# 2. Bootstrap remote state (one-time)
source .env
aws s3api create-bucket --bucket ecom-lakehouse-tf-state-${AWS_ACCOUNT_ID} --region us-east-1
aws s3api put-bucket-versioning --bucket ecom-lakehouse-tf-state-${AWS_ACCOUNT_ID} \
  --versioning-configuration Status=Enabled
aws dynamodb create-table --table-name ecom-lakehouse-tf-locks \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST --region us-east-1

# 3. Provision dev infrastructure
cd infra/envs/dev
terraform init
terraform apply -var-file="dev.tfvars"

# 4. Upload sample data
aws s3 cp Data/products.csv s3://ecom-lakehouse-raw-dev/products/2025/04/products.csv --profile personal
aws s3 cp "Data/orders_apr_2025.xlsx" s3://ecom-lakehouse-raw-dev/orders/2025/04/orders_apr_2025.xlsx --profile personal
aws s3 cp "Data/order_items_apr_2025.xlsx" s3://ecom-lakehouse-raw-dev/order_items/2025/04/order_items_apr_2025.xlsx --profile personal

# 5. Trigger pipeline (or wait for EventBridge)
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:us-east-1:647594457599:stateMachine:ecom-lakehouse-sm-dev \
  --input '{"raw_key":"orders/2025/04/orders_apr_2025.xlsx","dataset":"orders"}' \
  --profile personal

# 6. Tear down (ALWAYS run this after testing — $26 budget)
cd infra/envs/dev
terraform destroy -var-file="dev.tfvars" -var="protect_stateful=false"
```

## Repository Layout

```
ecom-lakehouse/
├── src/
│   ├── normalize/          # Lambda: xlsx/csv → Parquet (pandas/openpyxl)
│   ├── glue_jobs/          # Spark entrypoints (thin wrappers)
│   ├── lakehouse/          # Reusable Spark library (schemas, transforms, merge, etc.)
│   ├── lambdas/            # claim, archive, validate-schema helpers
│   ├── athena/             # Validation queries SQL
│   └── ui/                 # Streamlit dashboard (local dev)
├── infra/
│   ├── modules/            # Terraform modules (s3_zones, iam, glue, dynamodb, etc.)
│   └── envs/dev/           # Dev environment composition
├── orchestration/          # Step Functions ASL definition
├── tests/                  # Unit + integration tests + dirty fixtures
├── .github/workflows/      # ci.yml + deploy.yml
├── docs/                   # Full planning documentation (21 sub-plans)
└── .ai/                    # Architecture guide + status tracker
```

## Key Architectural Decisions

| ADR | Decision |
|-----|----------|
| ADR-002 | Lambda (pandas/openpyxl) normalizes xlsx→Parquet before Spark |
| ADR-005 | Tables unpartitioned + Z-Order; physical partition at ≥1 GB/partition |
| ADR-010 | Step Functions Standard (not Express — jobs run minutes) |
| ADR-015 | Athena v3 reads Delta natively (no symlink manifests, no MSCK REPAIR) |
| ADR-019 | DynamicFrames prohibited for Delta — Spark DataFrames only |
| ADR-020 | Glue: 2 workers fixed G.1X, auto-scaling off |
| ADR-021 | S3 policies: EnforceHTTPS + EnforceKMSEncryption Deny on every data bucket |

Full rationale in `docs/decision.md`. Architecture source of truth in `docs/architecture.md`.

## Development

```bash
# Install deps
pip install -e ".[dev]"

# Run tests
make test

# Lint
make lint

# Terraform plan (no apply)
make plan
```

## Progress Tracker

See `.ai/status.md` for build status updated after each sprint.

## Cost Awareness

AWS account budget: ~$26. Always run `terraform destroy` after smoke testing.  
Estimated cost per deploy+test+destroy cycle: < $1.00.  
See `.ai/status.md` Cost Tracking table for breakdown.
