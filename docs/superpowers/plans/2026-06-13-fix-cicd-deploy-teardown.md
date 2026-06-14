# Fix CI/CD, Deploy to AWS, Smoke Test, Teardown

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all CI/CD failures, bootstrap AWS infrastructure, run smoke test to validate the full pipeline, then tear it all down and write a status report.

**Architecture:** Fix code quality → fix CI config → bootstrap AWS with local credentials → set GitHub secrets → let CI/CD take over → smoke test → destroy.

**Tech Stack:** Python 3.11, PySpark 3.5, Delta Lake 3.2, Terraform ≥1.10, AWS (S3/Glue/Lambda/StepFunctions/DynamoDB/Athena/KMS/SNS/CloudWatch), GitHub Actions OIDC.

---

## Root Causes (diagnosed from CI logs)

| # | Failure | Fix |
|---|---------|-----|
| 1 | 15 flake8 errors (F401 unused imports, F841 unused vars, E501 line length, E302 blank lines) | Remove/fix in source files |
| 2 | `openpgp: key expired` on Terraform init | Bump `terraform_version` from `1.6.0` → `1.10.0` in both workflows |
| 3 | `aws-region not supplied` in OIDC step | Hardcode `aws-region: us-east-1` (not sensitive) |
| 4 | `TF_VAR_github_repo: ecom-lakehouse` | Repo is actually `project-2-lakehouse` — fix everywhere |
| 5 | Artifact upload before Terraform creates the bucket | Reorder: `terraform apply` → then upload scripts |
| 6 | AWS OIDC role / state bucket never bootstrapped | Manual one-time steps using local credentials |
| 7 | `Makefile` uses `personal` profile | Update to `sandbox-lakehouse-dev` |

---

## Files Changed

| File | What changes |
|------|-------------|
| `src/glue_jobs/ingest.py` | Remove unused `TABLE_PATH` import, unused `zorder_cols` var |
| `src/lakehouse/config.py` | Add blank line before function (E302) |
| `src/lakehouse/io.py` | Remove unused `config` import |
| `src/lakehouse/ledger.py` | Remove unused `DYNAMODB_LEDGER`, `DYNAMODB_WATERMARKS` imports |
| `src/lakehouse/logging_utils.py` | Remove unused `Optional`, `PROJECT_PREFIX` imports |
| `src/lakehouse/merge.py` | Remove unused `config` import |
| `src/lakehouse/schemas.py` | Wrap long line (E501) |
| `src/lakehouse/validation.py` | Remove unused `TABLE_PATH` import |
| `src/normalize/normalize_to_parquet.py` | Remove unused `hashlib`, `io` imports; remove unused `env` var |
| `src/ui/components/ledger.py` | Remove unused `Key` import |
| `src/ui/pages/1_pipeline_dashboard.py` | Remove unused `ui.config` import |
| `src/ui/pages/2_data_explorer.py` | Wrap long line |
| `src/ui/pages/3_data_quality.py` | Wrap 3 long lines |
| `tests/conftest.py` | Wrap 8 long lines |
| `tests/integration/test_full_pipeline.py` | Wrap 4 long lines |
| `.github/workflows/ci.yml` | `terraform_version: "1.10.0"` |
| `.github/workflows/deploy.yml` | `terraform_version: "1.10.0"`, hardcode `aws-region`, fix repo name, reorder apply before upload |
| `infra/envs/dev/dev.tfvars.example` | `github_repo = "project-2-lakehouse"` |
| `infra/envs/dev/variables.tf` | `default = "project-2-lakehouse"` |
| `Makefile` | `AWS_PROFILE=sandbox-lakehouse-dev` (all targets) |

---

## Task 1: Fix flake8 lint errors in src/

**Files:** `src/glue_jobs/ingest.py`, `src/lakehouse/*.py`, `src/normalize/normalize_to_parquet.py`, `src/ui/**/*.py`

- [ ] **Step 1: Fix `src/glue_jobs/ingest.py`** — remove unused import `TABLE_PATH` (line 39), remove unused variable `zorder_cols` (line 122)

The import line currently reads:
```python
from lakehouse.config import MERGE_KEY, ZORDER_COLS, TABLE_PATH, DATASET_TO_TABLE
```
Change to:
```python
from lakehouse.config import MERGE_KEY, ZORDER_COLS, DATASET_TO_TABLE
```

At line ~122, `zorder_cols` is assigned but never used. Remove the assignment — `ZORDER_COLS[args.dataset]` is accessed directly in `run_optimize()` calls later.

- [ ] **Step 2: Fix `src/lakehouse/io.py`** — remove unused `from lakehouse import config`

- [ ] **Step 3: Fix `src/lakehouse/ledger.py`** — remove unused imports `DYNAMODB_LEDGER`, `DYNAMODB_WATERMARKS` from the config import line

- [ ] **Step 4: Fix `src/lakehouse/logging_utils.py`** — remove unused `Optional` from typing import; remove unused `PROJECT_PREFIX` from config import

- [ ] **Step 5: Fix `src/lakehouse/merge.py`** — remove unused `from lakehouse import config`

- [ ] **Step 6: Fix `src/lakehouse/config.py`** — add a blank line before any function/class that flake8 flags E302 on (line 86)

- [ ] **Step 7: Fix `src/lakehouse/schemas.py` line 99** — wrap the long comment/line to stay under 100 chars

- [ ] **Step 8: Fix `src/lakehouse/validation.py`** — remove unused `TABLE_PATH` from config import

- [ ] **Step 9: Fix `src/normalize/normalize_to_parquet.py`**:
  - Remove `import hashlib` (line 28)
  - Remove `import io` (line 29)  
  - Remove unused local variable `env = event["env"]` (line 90) — `env` is extracted but only `dataset`, `batch_id`, etc. are used

- [ ] **Step 10: Fix `src/ui/components/ledger.py`** — remove unused `from boto3.dynamodb.conditions import Key`

- [ ] **Step 11: Fix `src/ui/pages/1_pipeline_dashboard.py`** — remove unused `from ui import config`

- [ ] **Step 12: Fix long lines** — wrap to ≤100 chars in:
  - `src/ui/pages/2_data_explorer.py:44`
  - `src/ui/pages/3_data_quality.py:95,121,175`

- [ ] **Step 13: Verify lint passes locally**
```bash
cd "d:\xcode\Amalitech\Phase_2_Project\Project 2 - Lakehouse Architecture"
flake8 src/ tests/
```
Expected: no output (zero errors)

- [ ] **Step 14: Commit**
```bash
git add src/ tests/
git commit -m "fix(lint): resolve all flake8 F401/F841/E501/E302 errors"
```

---

## Task 2: Fix tests/conftest.py and tests/ long lines

**Files:** `tests/conftest.py`, `tests/integration/test_full_pipeline.py`

- [ ] **Step 1: Wrap long lines in `tests/conftest.py`** at lines 154–156, 187–191 (all E501 >100 chars). Use line continuation `\` or break string concatenation across lines.

- [ ] **Step 2: Wrap long lines in `tests/integration/test_full_pipeline.py`** at lines 98–100, 106.

- [ ] **Step 3: Verify**
```bash
flake8 tests/
```
Expected: no output

- [ ] **Step 4: Commit**
```bash
git add tests/
git commit -m "fix(lint): wrap long lines in tests/ to satisfy E501"
```

---

## Task 3: Fix CI/CD configuration files

**Files:** `.github/workflows/ci.yml`, `.github/workflows/deploy.yml`, `infra/envs/dev/dev.tfvars.example`, `infra/envs/dev/variables.tf`, `Makefile`

- [ ] **Step 1: Bump Terraform version in `ci.yml`**

Change:
```yaml
terraform_version: "1.6.0"
```
To:
```yaml
terraform_version: "1.10.0"
```

- [ ] **Step 2: Fix `deploy.yml`** — four changes:

**a) Bump Terraform version:**
```yaml
terraform_version: "1.10.0"
```

**b) Hardcode `aws-region` (not a secret, no reason to use secrets for region):**
```yaml
- name: Configure AWS credentials (OIDC)
  uses: aws-actions/configure-aws-credentials@v4
  with:
    role-to-assume: ${{ secrets.AWS_ROLE_ARN }}
    aws-region: us-east-1
```

**c) Fix repo name in TF_VAR:**
```yaml
TF_VAR_github_repo: project-2-lakehouse
```

**d) Reorder: move artifact upload AFTER terraform apply** — on first deploy the artifacts bucket doesn't exist yet; Terraform creates it. Glue job definitions reference the S3 path but don't fail at apply time if the script isn't uploaded yet.

New order in deploy.yml:
```yaml
steps:
  - uses: actions/checkout@v4
  - uses: actions/setup-python@v5
    with:
      python-version: "3.11"
  - name: Configure AWS credentials (OIDC)
    ...
  - name: Install deps
    run: pip install -e ".[dev]"
  - name: Build lakehouse wheel
    run: pip wheel --no-deps -w dist/ .
  - uses: hashicorp/setup-terraform@v3
    with:
      terraform_version: "1.10.0"
  - name: Terraform apply (dev)
    working-directory: infra/envs/dev
    env:
      TF_VAR_account_id: ${{ secrets.AWS_ACCOUNT_ID }}
      TF_VAR_alert_email: ${{ secrets.ALERT_EMAIL }}
      TF_VAR_github_org: eli-bigman
      TF_VAR_github_repo: project-2-lakehouse
    run: |
      terraform init
      terraform apply -auto-approve
  - name: Upload artifacts to S3
    run: |
      aws s3 cp dist/*.whl s3://${{ secrets.S3_ARTIFACTS_BUCKET }}/wheels/lakehouse-latest.whl
      aws s3 cp src/glue_jobs/ingest.py s3://${{ secrets.S3_ARTIFACTS_BUCKET }}/scripts/ingest.py
      aws s3 cp src/glue_jobs/optimize.py s3://${{ secrets.S3_ARTIFACTS_BUCKET }}/scripts/optimize.py
  - name: Smoke test (trigger SF execution)
    run: python scripts/smoke_test.py
```

- [ ] **Step 3: Fix `dev.tfvars.example`** — change `github_repo = "ecom-lakehouse"` → `github_repo = "project-2-lakehouse"`

- [ ] **Step 4: Fix `variables.tf` default** — change `default = "ecom-lakehouse"` → `default = "project-2-lakehouse"`

- [ ] **Step 5: Fix `Makefile`** — change all `AWS_PROFILE=personal` → `AWS_PROFILE=sandbox-lakehouse-dev`

- [ ] **Step 6: Commit**
```bash
git add .github/workflows/ infra/envs/dev/dev.tfvars.example infra/envs/dev/variables.tf Makefile
git commit -m "fix(ci): bump Terraform to 1.10.0, fix repo name, hardcode region, reorder deploy steps"
```

---

## Task 4: AWS Bootstrap (manual, one-time — requires fresh credentials)

**Prerequisite:** User must refresh AWS credentials for `sandbox-lakehouse-dev` profile.

### 4a — Refresh credentials

The `sandbox-lakehouse-dev` profile uses static IAM credentials that are expired. Run:

```bash
# Option A: re-enter credentials via aws configure
aws configure --profile sandbox-lakehouse-dev
# Enter: AWS Access Key ID, AWS Secret Access Key, region=us-east-1, output=json

# Option B: if using SSO
aws sso login --profile sandbox-lakehouse-dev
```

Verify:
```bash
aws sts get-caller-identity --profile sandbox-lakehouse-dev
```
Expected: JSON with `"Account": "647594457599"`

### 4b — Create Terraform state bucket

```bash
aws s3api create-bucket \
  --bucket ecom-lakehouse-tf-state-647594457599 \
  --region us-east-1 \
  --profile sandbox-lakehouse-dev

aws s3api put-bucket-versioning \
  --bucket ecom-lakehouse-tf-state-647594457599 \
  --versioning-configuration Status=Enabled \
  --profile sandbox-lakehouse-dev

aws s3api put-bucket-encryption \
  --bucket ecom-lakehouse-tf-state-647594457599 \
  --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"aws:kms"},"BucketKeyEnabled":true}]}' \
  --profile sandbox-lakehouse-dev
```

### 4c — Create Terraform lock table

```bash
aws dynamodb create-table \
  --table-name ecom-lakehouse-tf-locks \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region us-east-1 \
  --profile sandbox-lakehouse-dev
```

### 4d — Create GitHub OIDC provider (one-time per account)

```bash
# Get the GitHub OIDC thumbprint
THUMBPRINT=$(echo | openssl s_client -servername token.actions.githubusercontent.com \
  -connect token.actions.githubusercontent.com:443 2>/dev/null | \
  openssl x509 -fingerprint -noout -sha1 | \
  sed 's/.*Fingerprint=//' | tr -d ':' | tr '[:upper:]' '[:lower:]')

aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list $THUMBPRINT \
  --profile sandbox-lakehouse-dev
```

### 4e — Bootstrap Terraform (creates the GHA deploy role + all infra)

First create `infra/envs/dev/dev.tfvars` (gitignored — do NOT commit this file):
```bash
cat > infra/envs/dev/dev.tfvars << 'EOF'
account_id       = "647594457599"
alert_email      = "richard.nutsugah@amalitechtraining.org"
github_org       = "eli-bigman"
github_repo      = "project-2-lakehouse"
protect_stateful = true
EOF
```

Then init and apply:
```bash
cd infra/envs/dev
AWS_PROFILE=sandbox-lakehouse-dev terraform init
AWS_PROFILE=sandbox-lakehouse-dev terraform plan -var-file=dev.tfvars
AWS_PROFILE=sandbox-lakehouse-dev terraform apply -var-file=dev.tfvars -auto-approve
```

Capture the GHA role ARN from the output:
```bash
AWS_PROFILE=sandbox-lakehouse-dev terraform output gha_deploy_role_arn
```

---

## Task 5: Set GitHub Repository Secrets

After Task 4e (Terraform apply complete), set all required secrets:

```bash
cd "d:\xcode\Amalitech\Phase_2_Project\Project 2 - Lakehouse Architecture"

# Get the GHA role ARN from Terraform output
GHA_ROLE_ARN=$(cd infra/envs/dev && AWS_PROFILE=sandbox-lakehouse-dev terraform output -raw gha_deploy_role_arn)

gh secret set AWS_ROLE_ARN       --body "$GHA_ROLE_ARN"
gh secret set AWS_ACCOUNT_ID     --body "647594457599"
gh secret set ALERT_EMAIL        --body "richard.nutsugah@amalitechtraining.org"
gh secret set S3_ARTIFACTS_BUCKET --body "ecom-lakehouse-artifacts-dev"
```

Verify secrets are set:
```bash
gh secret list
```
Expected: 4 secrets listed (AWS_ROLE_ARN, AWS_ACCOUNT_ID, ALERT_EMAIL, S3_ARTIFACTS_BUCKET)

---

## Task 6: Trigger and Verify CI/CD

- [ ] **Step 1: Push everything to main** (all commits from Tasks 1-3 are already local)
```bash
git push
```

- [ ] **Step 2: Watch CI run**
```bash
gh run watch
```
Expected: Both `CI` and `Deploy` jobs turn green.

- [ ] **Step 3: If CI lint job fails**, check specific error:
```bash
gh run view --log-failed 2>&1 | grep "error\|Error" | head -20
```

- [ ] **Step 4: If Deploy fails on OIDC**, verify the OIDC provider thumbprint is set and the role's trust policy has the correct `sub` claim (`repo:eli-bigman/project-2-lakehouse:ref:refs/heads/main`):
```bash
AWS_PROFILE=sandbox-lakehouse-dev aws iam get-role --role-name ecom-lakehouse-gha-deploy-role-dev \
  --query 'Role.AssumeRolePolicyDocument'
```

---

## Task 7: Smoke Test

After CI/CD green, manually upload sample data and trigger the pipeline:

- [ ] **Step 1: Upload sample data to raw zone**
```bash
AWS_PROFILE=sandbox-lakehouse-dev python scripts/upload_sample_data.py
```
Expected: Output showing 3 files uploaded to `s3://ecom-lakehouse-raw-dev/`

- [ ] **Step 2: Check smoke test passes** (deploy.yml already runs this, but run manually to verify):
```bash
AWS_PROFILE=sandbox-lakehouse-dev python scripts/smoke_test.py
```
Expected: Step Functions execution reaches `SUCCEEDED` status.

- [ ] **Step 3: Query Athena to verify data presence**
```bash
AWS_PROFILE=sandbox-lakehouse-dev aws athena start-query-execution \
  --query-string "SELECT COUNT(*) FROM ecom_lakehouse_db_dev.fct_orders" \
  --work-group ecom_lakehouse_wg_dev \
  --region us-east-1
```
Expected: Query returns row count > 0 (should be ~500 from the sample file).

- [ ] **Step 4: Verify all 3 tables have data**
```bash
for table in dim_products fct_orders fct_order_items; do
  echo "=== $table ==="
  AWS_PROFILE=sandbox-lakehouse-dev aws athena start-query-execution \
    --query-string "SELECT COUNT(*) FROM ecom_lakehouse_db_dev.$table" \
    --work-group ecom_lakehouse_wg_dev --region us-east-1
done
```
Expected: ~1000 products, ~500 orders, ~2768 order_items

- [ ] **Step 5: Verify DynamoDB ledger shows LOADED entries**
```bash
AWS_PROFILE=sandbox-lakehouse-dev aws dynamodb scan \
  --table-name ecom_lakehouse_ingestion_ledger_dev \
  --region us-east-1
```
Expected: 3 items (one per dataset), each with `status: LOADED`

---

## Task 8: Teardown

- [ ] **Step 1: Set `protect_stateful=false` in the local tfvars to allow bucket deletion**
```bash
cat > infra/envs/dev/dev.tfvars << 'EOF'
account_id       = "647594457599"
alert_email      = "richard.nutsugah@amalitechtraining.org"
github_org       = "eli-bigman"
github_repo      = "project-2-lakehouse"
protect_stateful = false
EOF
```

- [ ] **Step 2: Run terraform destroy**
```bash
cd infra/envs/dev
AWS_PROFILE=sandbox-lakehouse-dev terraform destroy \
  -var-file=dev.tfvars \
  -var="protect_stateful=false" \
  -auto-approve
```
Expected: All resources destroyed. Watch for any resources with `prevent_destroy` that may need manual override.

- [ ] **Step 3: Verify all resources gone**
```bash
AWS_PROFILE=sandbox-lakehouse-dev aws s3 ls | grep ecom-lakehouse
```
Expected: No output (except the Terraform state bucket — that's manual cleanup if desired)

- [ ] **Step 4: Delete Terraform state bucket (optional, saves ~$0)**
```bash
AWS_PROFILE=sandbox-lakehouse-dev aws s3 rb s3://ecom-lakehouse-tf-state-647594457599 --force
AWS_PROFILE=sandbox-lakehouse-dev aws dynamodb delete-table \
  --table-name ecom-lakehouse-tf-locks --region us-east-1
```

---

## Task 9: Write Status Report

**File:** `.ai/status.md`

- [ ] **Step 1: Update `.ai/status.md`** with the outcomes of the run. Include:
  - Session date and what was accomplished
  - Which CI/CD failures were fixed and how
  - Whether the full pipeline ran successfully (Step Functions SUCCEEDED/FAILED)
  - Row counts from Athena queries
  - Teardown success/failure
  - Any open issues or blockers

- [ ] **Step 2: Commit**
```bash
git add .ai/status.md
git commit -m "docs(status): record deploy/smoke-test/teardown outcomes for 2026-06-13 session"
git push
```

---

## Validation Checklist (before writing the report)

- [ ] `flake8 src/ tests/` — zero output
- [ ] GitHub Actions CI job — green
- [ ] GitHub Actions Deploy job — green
- [ ] `aws s3 ls | grep ecom-lakehouse` — shows 7 buckets (after apply, before destroy)
- [ ] Step Functions execution — `SUCCEEDED`
- [ ] Athena `dim_products` — ~1000 rows
- [ ] Athena `fct_orders` — ~500 rows
- [ ] Athena `fct_order_items` — ~2768 rows
- [ ] DynamoDB ledger — 3 entries with `status: LOADED`
- [ ] `terraform destroy` — completed cleanly
- [ ] `.ai/status.md` — updated with actual results
