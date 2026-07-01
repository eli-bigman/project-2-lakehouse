# E-Commerce Lakehouse: Infrastructure & AWS/Terraform Defense Guide

This guide is prepared to help you defend the **infrastructure, AWS services, and Terraform configuration** of the E-Commerce Lakehouse project.

---

## 1. Infrastructure-as-Code (IaC) with Terraform

All AWS resources are provisioned deterministically using Terraform.

### A. State Management & Locking
* **Remote State (S3):** To prevent losing the Terraform state file (`terraform.tfstate`) locally, we store it in a remote, versioned, and encrypted S3 bucket (`ecom-lakehouse-tf-state-<account_id>`).
* **State Locking (DynamoDB):** To prevent multiple developers or CI/CD pipelines from running `terraform apply` concurrently and corrupting the state, we use a DynamoDB table (`ecom-lakehouse-tf-locks`) as a distributed locking mechanism.

### B. Module Structure
The project uses modular Terraform definitions under `infra/modules/`:
* `s3/`: Configures 7 S3 buckets with encryption, public access blocks, and lifecycle policies.
* `dynamodb/`: Provisions ledger and watermark metadata tables.
* `iam/`: Configures service-linked roles with least-privilege policies.
* `lambda/`: Packages and deploys code for pre-processing and archiving.
* `glue/`: Defines Spark jobs and catalog database/tables.
* `stepfunctions/`: Deploys the ASL JSON definition as a state machine.

---

## 2. AWS Services Breakdown & S3 Bucket Design

Our architecture uses **7 S3 buckets** with explicit boundary roles:

| Bucket Name | Purpose | Lifecycle Policy / Key Config |
|---|---|---|
| `ecom-lakehouse-raw-dev` | Entry point for raw files (CSV/Excel) | Object versioning enabled |
| `ecom-lakehouse-staging-dev` | Normalized Parquet files | 7-day auto-expire rule (reduces cost) |
| `ecom-lakehouse-dwh-dev` | Production Delta Lake tables | Long-term analytical storage |
| `ecom-lakehouse-quarantine-dev` | Corrupt/rejected Parquet records | Used for debugging and auditing |
| `ecom-lakehouse-archive-dev` | Original raw files moved after loading | Audit trail |
| `ecom-lakehouse-athena-results-dev` | Athena query execution results | 30-day auto-expire rule |
| `ecom-lakehouse-artifacts-dev` | Stores Spark code wheels and Lambda zip packages | Versioned for deployment |

### S3 Cost Optimization:
We use **S3 Bucket Keys** with SSE-KMS encryption. Instead of generating a new KMS key call for every object write/read (which is expensive in high-throughput pipelines), Bucket Keys reuse a connection token, reducing KMS billing by up to 99%.

---

## 3. AWS Step Functions Orchestration

### A. How to Monitor Executions (The "Visual Tree Diagram")
If the reviewer asks how you monitor step-by-step pipeline runs:
1. Go to the **AWS Step Functions console**.
2. Select the state machine named `ecom-lakehouse-sm-dev`.
3. Under the **Executions** tab, click on any specific execution ID.
4. The dashboard displays the **Graph View**, showing the visual tree execution path:
   * **Green** blocks indicate successful stages.
   * **Red** blocks indicate failures.
   * **Grey/Blue** represent skipped or active steps.
5. Click on any block to inspect the **Input**, **Output**, or **Error Exception Stack Trace** (essential for debugging Lambda and Glue failures).

### B. Amazon States Language (ASL)
The state machine is defined using ASL (JSON format) in `orchestration/state_machine.json` and rendered dynamically by Terraform. It implements:
* **Choice States:** Branches on conditions like `already_processed: True` to skip files.
* **Error Catching & Retries:** Automatically retries Lambda failures up to 3 times with exponential backoff before failing and alerting the user.
* **SNS Alerting:** If a failure reaches the end catch block, Step Functions fires an SNS event to alert the data operations team via email.

---

## 4. Keyless CI/CD Security (GitHub OIDC)

> [!IMPORTANT]
> **No Long-Lived Access Keys in GitHub**
> We do not store static AWS Access Keys in GitHub Secrets. If those keys are leaked, the entire AWS account is compromised.

Instead, we use **OpenID Connect (OIDC)**:
1. An **IAM OIDC Identity Provider** is registered once in AWS for `token.actions.githubusercontent.com`.
2. Terraform provisions a deployment role (`gha-deploy-role`) with a trust policy allowing only our specific GitHub Organization and Repository to assume it.
3. During a GitHub Actions run, the runner requests a short-lived JSON Web Token (JWT) from GitHub, presents it to AWS STS (`AssumeRoleWithWebIdentity`), and obtains temporary credentials valid for 1 hour.

---

## 5. Security & Isolation (Least Privilege)
We enforce security at the IAM policy level:
* **Zone Isolation:** The Glue job role (`ecom-lakehouse-glue-ingest-role-dev`) can read from Staging S3 and write to DWH and Quarantine S3. It **cannot** write to Raw or Archive buckets.
* **Separation of Duty:** Only the `archive_file` Lambda has permissions to copy objects from Raw to Archive and subsequently delete from the Raw bucket. This prevents Spark code bugs from accidentally deleting raw incoming data.
