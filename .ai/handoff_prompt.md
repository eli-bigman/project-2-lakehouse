# Handoff Prompt — Music Streaming Lakehouse Project

Copy everything below the horizontal line and paste it as your first message in the new
project session.

---

You are picking up a music streaming data lakehouse project that is currently stalled at
the deployment stage. Your job is to finish it — get everything live on AWS, verify it is
running, then produce a set of documentation files and update the README. Here is the full
context and everything you need to do.

---

## What the project is

This is a production-grade data lakehouse on AWS for a music streaming platform, built
with the same medallion architecture pattern used in standard data engineering practice:
raw data lands in S3, gets cleaned and transformed through a pipeline, and is made
queryable via Amazon Athena. The core AWS services are S3, AWS Glue + Spark, Delta Lake,
AWS Step Functions, Glue Data Catalog, Athena, and GitHub Actions for CI/CD. IAM, Lambda,
DynamoDB, CloudWatch, SNS, KMS, and EventBridge are also part of the stack.

---

## Your mission — in order

### Step 1 — Understand the current state

Before doing anything, read the codebase carefully. Start with:
- `CLAUDE.md` or any equivalent project instructions file at the repo root
- `docs/` directory — especially the architecture doc and any decision log
- `infra/` directory — understand what Terraform modules exist and what they provision
- `.github/workflows/` — understand what CI and deploy workflows exist
- Any `.ai/` or `docs/` status file that tracks what has been done

You need to know: what infrastructure exists in the Terraform config, what AWS services
are being provisioned, what the datasets are, what CI/CD is doing, what state the project
was last known to be in, and whether there are any known issues or blocked steps.

---

### Step 2 — Assess what is already deployed on AWS

Check the current AWS state. Verify whether:
- The Terraform remote state S3 bucket and DynamoDB lock table exist (these are the
  bootstrap resources that must exist before `terraform init` can run)
- The GitHub Actions OIDC provider exists in the AWS account
- The main infrastructure has already been applied (check by listing S3 buckets, Glue
  jobs, Step Functions state machines, DynamoDB tables that match the project naming
  convention)
- The GitHub repository secrets are set (AWS_ROLE_ARN at minimum, plus any others the
  deploy workflow requires)
- The CI and Deploy workflows are passing or failing

Use AWS CLI commands with the correct profile. The AWS profile for this project is
`sandbox-musicstream-dev` (or whatever the correct profile name is — check the project's
`.env` or `Makefile`).

---

### Step 3 — Fix whatever is blocking deployment

This project is stalled at the deployment stage. Common issues at this stage:

**If CI is failing:**
- Run the test suite locally and fix any failures
- Common causes: code formatting not applied (run black + isort), missing test fixtures,
  import-time PySpark errors (F.col() called outside a SparkSession — wrap predicates in
  lambdas), missing `.secrets.baseline` file (run `python -m detect_secrets scan >
  .secrets.baseline` and commit it), Terraform version mismatch in the workflow

**If the deploy workflow is failing:**
- Check the exact error in the GitHub Actions logs: `gh run view <run-id> --log-failed`
- Common causes:
  - `failed to get shared config profile` — the provider has `profile = "..."` hardcoded;
    remove it and rely on AWS env vars injected by OIDC
  - `Not authorized to perform sts:AssumeRoleWithWebIdentity` — the OIDC trust policy
    sub claim does not match; check whether the workflow uses `environment: dev` (changes
    the sub to `environment:dev`) or not (sub is `ref:refs/heads/main`); the trust policy
    must list exactly what the workflow sends
  - State lock held — run `terraform force-unlock <lock-id>` after identifying the lock
    ID from the error
  - Wrong region — the sandbox may restrict to `eu-west-1` or `eu-central-1` only;
    update all region references in backend.tf, providers.tf, and workflow files
  - Wrong account ID — verify with `aws sts get-caller-identity`
  - S3 upload denied — bucket policy requires `--sse aws:kms` on every upload; add
    `--sse aws:kms` to all `aws s3 cp` commands and `ServerSideEncryption='aws:kms'` to
    all boto3 `put_object` calls

**If Terraform bootstrap has not been done:**
- Create the state bucket manually: `aws s3api create-bucket --bucket <state-bucket-name>
  --region <region> --profile <profile>`
- Enable versioning: `aws s3api put-bucket-versioning ...`
- Create the lock table: `aws dynamodb create-table --table-name <lock-table-name>
  --attribute-definitions AttributeName=LockID,AttributeType=S
  --key-schema AttributeName=LockID,KeyType=HASH --billing-mode PAY_PER_REQUEST
  --region <region> --profile <profile>`

**If GitHub secrets are missing:**
- Get the GHA deploy role ARN from Terraform output: `terraform output gha_deploy_role_arn`
- Set: `gh secret set AWS_ROLE_ARN --body "<arn>"`
- Set any other secrets the deploy workflow references (AWS_ACCOUNT_ID, ALERT_EMAIL,
  S3_ARTIFACTS_BUCKET, etc.)

Work through these issues one at a time, committing fixes incrementally. Each commit
message should explain what was broken and why the fix works.

**Important constraints:**
- Use GitHub OIDC for AWS authentication — never store static AWS credentials in the repo
  or GitHub secrets
- The IAM trust policy must use `StringEquals` (not `StringLike`) on the `sub` claim
- All S3 uploads must include `--sse aws:kms` if the bucket policies enforce KMS encryption
- Glue Spark jobs must use native Spark DataFrames, never DynamicFrames, when writing to
  Delta tables
- If the project uses Glue workers, the AWS API minimum is 2 workers — setting 1 is an
  API error

---

### Step 4 — Get the deploy workflow green

Push to main and watch the deploy workflow run to completion. It must pass every step:
OIDC authentication, Terraform apply, artifact upload to S3, and any smoke test. If any
step fails, diagnose from the logs and fix it.

Once the deploy workflow is green, verify on AWS:
- S3 buckets exist and are accessible
- DynamoDB tables exist
- Glue job exists
- Step Functions state machine exists
- Athena workgroup and Glue Data Catalog database exist

---

### Step 5 — Run a smoke test

Trigger a pipeline execution. Upload a small sample file to the raw S3 bucket and start
a Step Functions execution. Verify that the execution starts and reaches a terminal state
(SUCCEEDED if the pipeline code is implemented, or FAILED if it is still placeholder code
— either proves the infrastructure is operational). Check the DynamoDB ingestion ledger
for the batch record.

---

### Step 6 — Tear down

Set any `prevent_destroy` overrides needed, then run `terraform destroy` to remove all
resources. Verify that the main project resources are gone. The Terraform state bucket
and DynamoDB lock table (bootstrapped manually, not managed by Terraform) should remain.

---

### Step 7 — Produce documentation

Once the deploy-and-destroy cycle is confirmed working, produce the following files in
the `.ai/` directory. Each file should be specific to this music streaming project — its
datasets, its architecture, its actual decisions. Do not produce generic documentation.

**`.ai/architecture_decisions.md`**

A detailed explanation of every significant design decision in the project. For each
decision, explain: what the context was, what was decided, why it was decided that way,
what alternatives were considered and rejected, and what the downstream consequences are.
Cover: the overall medallion architecture, why each AWS service was chosen, the
normalization approach, the ETL compute choices, deduplication strategy, partitioning
decisions, the DynamoDB control plane (if present), the IAM and security design, the KMS
strategy, storage tiering choices, any sandbox constraints that affected deployment
(region restrictions, account limits), and what was actually deployed to AWS. Write this
for someone who needs to understand the system deeply, not just operate it. No Q&A format.

**`.ai/how_to_test.md`**

A complete practical guide to testing the application. Cover: local unit and integration
tests (commands, what they test, any setup required), linting and formatting checks, how
to run Terraform validate and plan locally, how to trigger an end-to-end pipeline
execution manually on AWS (upload file, start execution, monitor it), how to verify each
stage succeeded (check DynamoDB ledger, check quarantine zone, query Athena for row
counts), how to verify idempotency, how to check CloudWatch logs when something fails,
and the teardown procedure. Include the actual AWS CLI commands with real bucket names,
table names, and ARNs from the deployed infrastructure.

**`.ai/terraform_explained.md`**

A from-first-principles explanation of Terraform aimed at someone new to it, using
only examples from this project. Cover: what Terraform is and why, state and remote
state, the state lock (DynamoDB), providers (what they are, which ones this project
uses and why), the backend configuration, resources, variables and how they are supplied
(tfvars, TF_VAR_ env vars), outputs, modules (what they are, the full module structure
of this project, how they depend on each other), data sources, `for_each` and the rename
danger, `lifecycle` blocks (prevent_destroy, force_destroy), locals, the lock file, the
full deploy sequence from scratch, how CI/CD uses Terraform without a local profile, and
common errors encountered in this project with their fixes.

**`.ai/interview_qa.md`**

A comprehensive set of questions and answers that a senior data engineer would ask in
a technical interview about this specific project. Cover six areas: (1) architecture and
the big picture — end-to-end flow, why each architectural choice was made; (2) AWS
services — why each service was chosen over alternatives, how they are configured, what
the tradeoffs are; (3) data engineering concepts — schema enforcement, deduplication,
MERGE mechanics, partitioning decisions, validation strategy; (4) Terraform — state,
modules, dependency management, safety features; (5) CI/CD — pipeline stages, why each
check exists, how OIDC works; (6) operational — diagnosing failures, re-running failed
batches, extending the pipeline, scaling considerations. Answers should be detailed and
specific to this project, not generic textbook answers. The goal is that reading this
file prepares someone to answer deep technical questions about every aspect of the system.

---

### Step 8 — Update the README

Rewrite the README to explain what the project is and why it exists — as a real data
engineering system someone built, not a school exercise. It should read like a project
a data engineer would put on their portfolio. Include: a clear one-paragraph summary of
what the system does, the data pipeline flow as an ASCII diagram, what datasets it
processes (with actual field names and row counts if known), the key architectural
decisions worth calling out (without mentioning any brief or instructions document),
the full repository layout annotated with what each directory does, the infrastructure
overview (what Terraform provisions), the CI/CD setup, the development quickstart, and
cost notes. No emojis. No mention of any brief, instructions, or project specification.

---

## Commit cadence

Commit after every meaningful unit of work:
- Fix a CI failure → commit
- Fix a deploy workflow issue → commit
- Successful deploy confirmed → commit
- Smoke test passing → commit
- Each documentation file created → commit
- README updated → commit

Each commit message should be specific: what changed, why, what it fixes.

---

## What success looks like

When you are done:
1. The CI workflow passes (lint, security, tests, terraform validate) — green on GitHub
2. The deploy workflow passes (OIDC auth, terraform apply, artifact upload, smoke test) — green on GitHub
3. The infrastructure was verified live on AWS
4. `terraform destroy` completed successfully
5. `.ai/architecture_decisions.md` exists and is specific to this project
6. `.ai/how_to_test.md` exists with real commands from the deployed infrastructure
7. `.ai/terraform_explained.md` exists and explains Terraform using this project's code
8. `.ai/interview_qa.md` exists with deep Q&A covering every part of the system
9. `README.md` reads like a real project, not a submission
10. Everything is committed and pushed to main
