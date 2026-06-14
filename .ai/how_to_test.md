# How to Test the ecom-lakehouse Application

This covers every level of testing — local unit/integration tests, CI verification,
manual end-to-end testing against a live AWS environment, and what to check when
something goes wrong.

---

## Prerequisites

```bash
# Python 3.11, AWS CLI v2, Terraform >= 1.10
pip install -e ".[dev]"

# AWS credentials loaded for the sandbox-lakehouse-dev profile
.\scripts\set_aws_profile.ps1   # Windows — loads credentials from .env
# or: export AWS_PROFILE=sandbox-lakehouse-dev
```

---

## 1. Local Unit and Integration Tests

Run the full test suite without touching AWS at all:

```bash
# All tests
make test

# Unit tests only (fast — no Spark cluster startup)
pytest tests/unit/ -v

# Integration tests only (slower — local SparkSession + Delta)
pytest tests/integration/ -v

# Single test file
pytest tests/unit/test_validation.py -v
```

The unit tests exercise the `src/lakehouse/` library: schema definitions, validation
rules, hash computation, and MERGE key generation. They use a local SparkSession created
in `conftest.py` — no AWS credentials or network access.

The integration tests run the full Glue job logic against a local Delta warehouse in a
temp directory. They verify that the MERGE produces the correct deduplication behavior,
that quarantine records are written for invalid rows, and that the DynamoDB ledger
contract is respected (tested with mocked calls).

One important note about PySpark and module-level code: `F.col()` expressions must not
be evaluated at module import time. They assert that a SparkContext is active. All
validation rule predicates in `src/lakehouse/validation.py` are wrapped in zero-argument
lambdas and only evaluated when a SparkSession exists.

---

## 2. Lint and Format Checks

```bash
# Run all linters (same checks as CI)
make lint

# Format code in-place (do this before committing)
python -m black src/ tests/
python -m isort src/ tests/

# Check only (does not modify — this is what CI runs)
python -m black --check src/ tests/
python -m isort --check src/ tests/
python -m flake8 src/ tests/
```

The CI pipeline runs black, isort, flake8, and detect-secrets. All must pass. If black
reports failures, it means files were not formatted before committing — run black locally
and commit the result. The `.flake8` file configures the line length and per-file ignores.

---

## 3. Terraform Validation

```bash
make validate
# or:
cd infra/envs/dev && terraform validate
```

This checks HCL syntax and provider schema compatibility without making any API calls.
Run this before pushing any Terraform changes.

```bash
# See what Terraform would change (no apply)
make plan
# or:
cd infra/envs/dev
AWS_PROFILE=sandbox-lakehouse-dev terraform plan -var-file="dev.tfvars"
```

---

## 4. CI Pipeline Verification

Every push to any branch triggers the CI workflow (`.github/workflows/ci.yml`). Watch it at:

```
https://github.com/eli-bigman/project-2-lakehouse/actions
```

The CI runs five stages in sequence:

1. **Lint** — black, isort, flake8
2. **Security** — detect-secrets baseline scan
3. **Unit tests** — pytest tests/unit/
4. **Integration tests** — pytest tests/integration/
5. **Terraform validate** — validate all module HCL

Every push to `main` additionally triggers the Deploy workflow.

---

## 5. End-to-End Testing Against Live AWS

### 5a. Check the infrastructure is up

```powershell
$env:AWS_PROFILE = "sandbox-lakehouse-dev"

# Verify S3 buckets exist
aws s3 ls | grep ecom-lakehouse

# Verify DynamoDB tables
aws dynamodb list-tables --region eu-west-1

# Verify the Step Functions state machine
aws stepfunctions list-state-machines --region eu-west-1

# Verify the Glue job
aws glue get-job --job-name ecom-lakehouse-ingest-dev --region eu-west-1
```

### 5b. Upload a sample file to raw

The raw bucket key layout is: `<dataset>/<yyyy>/<mm>/<filename>`

```powershell
# Products (CSV)
aws s3 cp Data/products.csv `
  s3://ecom-lakehouse-raw-dev/dim_products/2025/04/products.csv `
  --sse aws:kms --profile sandbox-lakehouse-dev

# Orders (xlsx)
aws s3 cp "Data/orders_apr_2025.xlsx" `
  s3://ecom-lakehouse-raw-dev/fct_orders/2025/04/orders_apr_2025.xlsx `
  --sse aws:kms --profile sandbox-lakehouse-dev

# Order items (xlsx)
aws s3 cp "Data/order_items_apr_2025.xlsx" `
  s3://ecom-lakehouse-raw-dev/fct_order_items/2025/04/order_items_apr_2025.xlsx `
  --sse aws:kms --profile sandbox-lakehouse-dev
```

Note: every upload must include `--sse aws:kms`. The bucket policy denies PutObject
without the KMS encryption header (ADR-021).

### 5c. Trigger a pipeline execution

```powershell
$env:AWS_PROFILE = "sandbox-lakehouse-dev"

# Start a Step Functions execution for orders
aws stepfunctions start-execution `
  --state-machine-arn arn:aws:states:eu-west-1:970547336735:stateMachine:ecom-lakehouse-sm-dev `
  --name "manual-test-$(Get-Date -Format yyyyMMddHHmmss)" `
  --input '{"dataset": "fct_orders", "batch_id": "2025-04-manual", "source_key": "fct_orders/2025/04/orders_apr_2025.xlsx"}' `
  --region eu-west-1
```

### 5d. Monitor execution progress

```powershell
# List recent executions
aws stepfunctions list-executions `
  --state-machine-arn arn:aws:states:eu-west-1:970547336735:stateMachine:ecom-lakehouse-sm-dev `
  --region eu-west-1

# Describe a specific execution (replace with actual execution ARN)
aws stepfunctions describe-execution `
  --execution-arn <EXECUTION_ARN> `
  --region eu-west-1

# Get execution history (step-by-step state transitions)
aws stepfunctions get-execution-history `
  --execution-arn <EXECUTION_ARN> `
  --region eu-west-1
```

The Step Functions console at `https://eu-west-1.console.aws.amazon.com/states` gives
a visual graph of each execution — which states succeeded, which failed, and the input/
output at each step.

### 5e. Verify the DynamoDB ledger

After a successful run, the ingestion ledger should show the batch as LOADED:

```powershell
aws dynamodb scan `
  --table-name ecom_lakehouse_ingestion_ledger_dev `
  --region eu-west-1
```

Look for a row with `status: LOADED`, `raw_count`, `clean_count`, and `reject_count`.
The clean and reject counts should sum to the raw count.

### 5f. Query the Delta table via Athena

```powershell
# Start an Athena query
aws athena start-query-execution `
  --query-string "SELECT COUNT(*) FROM ecom_lakehouse_db_dev.fct_orders LIMIT 1" `
  --work-group ecom_lakehouse_wg_dev `
  --region eu-west-1

# The response gives a QueryExecutionId. Check its status:
aws athena get-query-execution `
  --query-execution-id <ID> `
  --region eu-west-1

# Get results
aws athena get-query-results `
  --query-execution-id <ID> `
  --region eu-west-1
```

Expected results: `fct_orders` should have 500 rows, `fct_order_items` 2,768 rows,
`dim_products` 1,000 rows (from the provided sample data).

### 5g. Run the automated smoke test

```powershell
$env:AWS_REGION = "eu-west-1"
$env:AWS_ACCOUNT_ID = "970547336735"
$env:S3_RAW_BUCKET = "ecom-lakehouse-raw-dev"
python scripts/smoke_test.py
```

The smoke test uploads a canary CSV, starts a Step Functions execution, and polls for
a terminal state. At the current sprint stage (placeholder Lambda/Glue code), the
execution will FAIL at the code level — but the test reports `INFRA_OK` because the
infrastructure (S3 bucket writable, Step Functions reachable, execution started and
tracked) is confirmed operational.

---

## 6. Verifying Quarantine

To test that bad records are quarantined rather than failing the run, upload a file with
a null primary key:

```python
# Create a test CSV with a null product_id
import boto3
bad_csv = "product_id,department_id,department,product_name\n,1,Books,Bad Product\n"
s3 = boto3.client("s3", region_name="eu-west-1")
s3.put_object(
    Bucket="ecom-lakehouse-raw-dev",
    Key="dim_products/2025/05/bad_products.csv",
    Body=bad_csv.encode(),
    ServerSideEncryption="aws:kms",
)
```

After the run, check the quarantine bucket:

```powershell
aws s3 ls s3://ecom-lakehouse-quarantine-dev/ --recursive --profile sandbox-lakehouse-dev
```

The quarantined file should contain the bad row with a `reject_reason` column explaining
why it was rejected (`null_product_id`).

---

## 7. Checking CloudWatch Logs and Alarms

Glue job logs are in CloudWatch under `/aws-glue/jobs/output` and `/aws-glue/jobs/error`.

```powershell
# List log groups
aws logs describe-log-groups --region eu-west-1

# Tail a Glue job log (replace job-run-id with actual value from Step Functions output)
aws logs get-log-events `
  --log-group-name "/aws-glue/jobs/output" `
  --log-stream-name "<JOB-RUN-ID>" `
  --region eu-west-1
```

The CloudWatch dashboard (`ecom-lakehouse-dev`) shows Step Functions execution counts
(started, succeeded, failed) and Glue driver run times. Access it at:
`https://eu-west-1.console.aws.amazon.com/cloudwatch/home?region=eu-west-1#dashboards`

Pipeline failure alerts go to the SNS topic → email subscription you confirmed when
Terraform applied the `aws_sns_topic_subscription`. If the Step Functions execution
fails, you should receive an email. Check the CloudWatch alarm
`ecom-lakehouse-sf-failed-dev` for alarm history.

---

## 8. Idempotency Test

Run the same pipeline twice on the same file and verify the row count does not change:

1. Execute Step Functions with `batch_id = "2025-04-idempotency-test"`.
2. After it completes (`LOADED` in DynamoDB), execute it again with the same `batch_id`.
3. The second execution should skip processing (DynamoDB conditional write will see the
   existing LOADED status and short-circuit the workflow).
4. Query Athena — the row count must be identical after both executions.

---

## 9. Teardown

After testing, destroy all resources to avoid ongoing costs:

```powershell
cd "infra\envs\dev"
$env:AWS_PROFILE = "sandbox-lakehouse-dev"

# Step 1: disable prevent_destroy on stateful buckets
terraform apply -auto-approve -var-file="dev.tfvars" -var="protect_stateful=false"

# Step 2: destroy everything
terraform destroy -auto-approve -var-file="dev.tfvars" -var="protect_stateful=false"
```

Verify teardown is complete:

```powershell
# Should return empty or only show the Terraform state bucket (manually bootstrapped)
aws s3 ls | grep ecom-lakehouse
```

The Terraform state bucket (`ecom-lakehouse-tf-state-970547336735`) and DynamoDB lock
table (`ecom-lakehouse-tf-locks`) were bootstrapped manually and are not managed by
Terraform — they survive destroy intentionally and should be removed manually if the
project is permanently decommissioned.
