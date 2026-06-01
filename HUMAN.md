# HUMAN.md — Operator Setup Guide

> **Who this is for:** the person standing up this project on AWS for the first time.
> Everything here is a human action — things Terraform, CI/CD, or code can't do for you.
> Read this once top-to-bottom before touching anything. It is deliberately ordered by
> dependency: later steps assume earlier ones are done.

---

## 0. Quick-start checklist

Before starting, confirm you have:
- [ ] An AWS account where you can create IAM resources
- [ ] A GitHub account with a repository for this project
- [ ] Python 3.11+ and pip installed locally
- [ ] AWS CLI v2 installed and configured
- [ ] Terraform >= 1.6 installed
- [ ] Git installed

---

## 1. IAM access — recommended approach

> **You asked whether creating a new IAM role is overdoing it. Short answer: for your
> own developer access, no new role is needed — use what you already have. The one role
> you DO need to create manually is the GitHub Actions OIDC role. Everything else is
> provisioned by Terraform.**

### Option A — Recommended (modern, no long-lived keys)
Use **AWS IAM Identity Center (SSO)** if your account supports it:
```bash
# One-time setup in the AWS console:
# 1. Enable IAM Identity Center in your AWS account
# 2. Create a permission set: "LakehouseAdmin" → AdministratorAccess
# 3. Assign your user to the permission set
# 4. Run:
aws configure sso
aws sso login --profile lakehouse-dev
export AWS_PROFILE=lakehouse-dev
```
No long-lived access keys ever written to disk. This is AWS's current recommendation.

### Option B — Simple (personal account, legacy)
If Identity Center feels like overkill for a solo project on a personal account:
1. Go to IAM → Users → your user (or create one if you only have root)
2. Enable **MFA** on that user — non-negotiable
3. Attach `AdministratorAccess` directly (you can tighten this later)
4. Generate an access key pair **only if you can't use SSO**
5. Run `aws configure` and paste the key + secret

> **Do not use your root account for any of this.** Root should only be used to set up
> your first IAM user/Identity Center, then locked away with MFA.

### What about all the Lambda/Glue/Step Functions roles?
**You don't create those manually.** They are defined in `infra/modules/iam/` and
provisioned by `terraform apply`. The roles listed in `docs/security_iam.md` are
*service* roles for AWS services — Terraform owns them. The only human-access role you
might create manually is the OIDC trust role for GitHub Actions (see §4 below).

---

## 2. Local prerequisites

Install these before running any commands:

```bash
# AWS CLI v2
# https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html
aws --version   # expect: aws-cli/2.x.x

# Terraform >= 1.6
# https://developer.hashicorp.com/terraform/install
terraform -version

# Python 3.11+
python --version

# Project Python dependencies (once src/ exists)
pip install -r requirements.txt

# Terraform linting / security (CI uses these too)
pip install checkov
brew install tflint    # or: https://github.com/terraform-linters/tflint#installation
```

---

## 3. Things you must decide before running Terraform

Fill in `.env` (copied from `.env.example`) with your choices:

| Variable | What to decide | Notes |
|----------|---------------|-------|
| `AWS_REGION` | Which region | We assume `us-east-1`; pick one and stick with it |
| `AWS_ACCOUNT_ID` | Your 12-digit account ID | `aws sts get-caller-identity --query Account` |
| `TF_ENV` | `dev` or `prod` | Start with `dev`; use `prod` only for production go-live |
| `ALERT_EMAIL` | Email for SNS failure alerts | Must be confirmed after first `terraform apply` |
| `GITHUB_ORG` | Your GitHub username or org | Used to scope the OIDC trust policy |
| `GITHUB_REPO` | Repository name | e.g. `ecom-lakehouse` |

All S3 bucket names, DynamoDB table names, and other resource names are derived from
`PROJECT_PREFIX` + `TF_ENV` — you don't choose them individually. See `.env.example`
for the full pattern.

---

## 4. GitHub repository setup (do once, before CI/CD)

### 4a. Create the OIDC identity provider (one-time, manual)
This allows GitHub Actions to assume an AWS role without storing access keys:
```bash
# Run this once in your AWS account:
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1
```
Then Terraform creates the `gha-deploy-role` that trusts this provider (scoped to your
`main` branch). This is the **only** IAM role you create manually — everything else is
Terraform-managed.

### 4b. Add GitHub repository secrets
Go to **GitHub → your repo → Settings → Secrets and variables → Actions** and add:

| Secret name | Value | Notes |
|-------------|-------|-------|
| `AWS_ROLE_ARN` | ARN of `gha-deploy-role` | Created by Terraform bootstrap (§6) |
| `AWS_REGION` | e.g. `us-east-1` | Must match your `.env` |
| `TF_BACKEND_BUCKET` | Name of your Terraform state bucket | Created in §6 |
| `TF_BACKEND_LOCK_TABLE` | Name of the DynamoDB lock table | Created in §6 |

### 4c. Set branch protection on `main`
Go to **Settings → Branches → Add rule for `main`**:
- [x] Require status checks to pass (add `ci / lint-test`)
- [x] Require pull request before merging
- [x] Do not allow bypassing the above rules

---

## 5. AWS services to enable manually

Some AWS services are not on by default. Enable these in the console before Terraform runs:

| Service | Where | Why |
|---------|-------|-----|
| **S3 Block Public Access** (account level) | S3 → Block Public Access settings | Prevents any bucket in the account from going public |
| **AWS Glue** | Just needs to be used once | Auto-activates; no manual step |
| **Amazon Athena** | Athena → Settings | Set a default results bucket (we override per workgroup, but Athena must be initialized) |
| **AWS CloudTrail** | CloudTrail → Create trail | Enable management + S3 data events for audit |
| **Amazon SNS email confirmation** | Automatic | After `terraform apply`, AWS sends a confirmation email to `ALERT_EMAIL` — you must click the link or alerts won't fire |

---

## 6. Terraform state bootstrap (one-time, manual)

Terraform needs a remote state bucket and a DynamoDB lock table **before** it can manage
anything else. Create these manually once:

```bash
# 1. Create the state bucket (replace ACCOUNT_ID and REGION)
aws s3api create-bucket \
  --bucket ecom-lakehouse-tf-state-ACCOUNT_ID \
  --region us-east-1

# 2. Enable versioning on the state bucket
aws s3api put-bucket-versioning \
  --bucket ecom-lakehouse-tf-state-ACCOUNT_ID \
  --versioning-configuration Status=Enabled

# 3. Block public access on the state bucket
aws s3api put-public-access-block \
  --bucket ecom-lakehouse-tf-state-ACCOUNT_ID \
  --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

# 4. Create the DynamoDB lock table
aws dynamodb create-table \
  --table-name ecom-lakehouse-tf-locks \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region us-east-1
```

Then set `TF_BACKEND_BUCKET` and `TF_BACKEND_LOCK_TABLE` in your `.env` and GitHub
Secrets to match the names you used above.

---

## 7. Environment configuration

```bash
# Copy the example file and fill in your values
cp .env.example .env

# Never commit .env — it's in .gitignore
# The .env is for local development only.
# CI/CD uses GitHub Secrets instead.
```

See `.env.example` for every variable with inline documentation. The minimum set you
**must** fill before running Terraform:
- `AWS_ACCOUNT_ID`
- `AWS_REGION`
- `TF_ENV`
- `TF_BACKEND_BUCKET`
- `TF_BACKEND_LOCK_TABLE`
- `ALERT_EMAIL`
- `GITHUB_ORG`
- `GITHUB_REPO`

---

## 8. Data files — what to put in S3

The three sample files in `Data/` are your initial test data. Once Terraform has created
the raw bucket, upload them like this:

```bash
# Set your env
source .env   # or: export $(cat .env | xargs)

# Upload products (CSV)
aws s3 cp Data/products.csv \
  s3://ecom-lakehouse-raw-${TF_ENV}/products/2025/04/products.csv

# Upload orders (keep original xlsx)
aws s3 cp "Data/orders_apr_2025.xlsx" \
  s3://ecom-lakehouse-raw-${TF_ENV}/orders/2025/04/orders_apr_2025.xlsx

# Upload order items
aws s3 cp "Data/order_items_apr_2025.xlsx" \
  s3://ecom-lakehouse-raw-${TF_ENV}/order_items/2025/04/order_items_apr_2025.xlsx
```

These uploads will trigger the EventBridge rule → Step Functions pipeline once the
infrastructure is deployed.

---

## 9. First deployment sequence

Follow the sprint order from `docs/sprint_planning.md`. For your first run:

```bash
# Sprint 1: provision dev infrastructure
cd infra/envs/dev
terraform init
terraform plan -var-file="dev.tfvars"
terraform apply -var-file="dev.tfvars"

# Confirm the SNS email you received

# Sprint 2–3: deploy Glue scripts (CI/CD does this on push to main)
git push origin main   # triggers deploy.yml

# Upload sample data (§8 above)

# Smoke test: watch the execution in the AWS console
# Step Functions → ecom-lakehouse-state-machine-dev → Executions
```

---

## 10. Streamlit UI (local dev, for testing user stories)

Once the DWH is populated (after Sprint 3–4), run the UI locally:

```bash
# Install UI deps
pip install streamlit boto3 awswrangler

# Set env vars (the UI reads from .env)
source .env

# Run
streamlit run src/ui/app.py
```

The UI connects to Athena using your local AWS credentials. No separate deployment
needed for development — just run it locally against the `dev` environment.

For a deployed version (optional), see `docs/ui_streamlit.md`.

---

## 11. Verifying everything works

After each sprint, run these checks:

```bash
# Infra health
terraform plan   # should show: No changes

# IAM policy simulation (zone isolation)
aws iam simulate-principal-policy \
  --policy-source-arn arn:aws:iam::ACCOUNT_ID:role/ecom-lakehouse-glue-ingest-role-dev \
  --action-names s3:DeleteObject \
  --resource-arns arn:aws:s3:::ecom-lakehouse-raw-dev/*

# S3 encryption enforcement — should return 403
aws s3api put-object \
  --bucket ecom-lakehouse-raw-dev \
  --key test.txt \
  --body /dev/null \
  --server-side-encryption AES256   # expect: AccessDenied

# Athena query after a pipeline run
aws athena start-query-execution \
  --query-string "SELECT COUNT(*) FROM fct_orders" \
  --query-execution-context Database=ecom_lakehouse_db_dev \
  --work-group ecom_lakehouse_wg_dev \
  --result-configuration OutputLocation=s3://ecom-lakehouse-athena-results-dev/

# Step Functions smoke test (triggers the full pipeline)
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:REGION:ACCOUNT_ID:stateMachine:ecom-lakehouse-sm-dev \
  --input '{"raw_key":"orders/2025/04/orders_apr_2025.xlsx"}'
```

---

## 12. Secrets you must NEVER commit

| Secret type | Where it lives | Never put in |
|-------------|---------------|--------------|
| AWS access keys | `~/.aws/credentials` or SSO | `.env`, code, git |
| `.tfvars` with real values | local only | git (it's in `.gitignore`) |
| `.env` | local only | git |
| KMS key material | AWS-managed | anywhere |
| SNS subscriber email | AWS console | code |

Run `detect-secrets scan` before any commit if you're ever unsure.

---

## Reference

- Architecture & resource names → `docs/architecture.md` §3
- Service roles (what Terraform creates) → `docs/security_iam.md`
- Sprint order → `docs/sprint_planning.md`
- Full planning docs → `docs/master_plan.md`
- Stuck? → `docs/reference.md`
