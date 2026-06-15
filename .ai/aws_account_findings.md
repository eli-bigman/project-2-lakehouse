# AWS Account Findings — sandbox-lakehouse-dev

**Date surveyed:** 2026-06-15  
**Profile:** `sandbox-lakehouse-dev`  
**Account:** `970547336735`  
**Region:** `us-east-1`  
**Identity:** `arn:aws:sts::970547336735:assumed-role/DCEPrincipal-dce/8YwdgYbhuI__richard+nutsugah+amalitech+com`

---

## 1. What kind of account this is

This is a **DCE (Disposable Cloud Environment) sandbox** — a time-leased AWS account provisioned by Amalitecch Training for course/project work. Key implications:

- **Credentials are temporary STS tokens** — they expire. Re-run `scripts/set_aws_profile.ps1` to refresh.
- **The account will be auto-wiped** when the lease ends. Do not treat resources here as durable.
- **`DCEPrincipalDefaultPolicy-dce`** explicitly denies IAM introspection (can't call `iam:GetRole` on your own role, can't list your own policies). This is standard DCE lockdown.
- **`DCEAdmin`** role exists in the account — the admin role used by the training platform to manage the lease.

---

## 2. Your lakehouse IAM roles — already deployed

All 6 project-specific roles were **created on 2026-06-14** (yesterday), tagged with `ManagedBy: terraform`. The Terraform infra bootstrap has already run.

| Role | Trust Principal | Attached Policy | Purpose |
|------|----------------|-----------------|---------|
| `ecom-lakehouse-glue-ingest-role-dev` | `glue.amazonaws.com` | `AWSGlueServiceRole` | Glue Spark ETL jobs |
| `ecom-lakehouse-normalize-lambda-role-dev` | `lambda.amazonaws.com` | `AWSLambdaBasicExecutionRole` | Normalization Lambda (xlsx/csv → Parquet) |
| `ecom-lakehouse-archive-lambda-role-dev` | `lambda.amazonaws.com` | `AWSLambdaBasicExecutionRole` | Archive/lifecycle Lambda |
| `ecom-lakehouse-stepfunctions-role-dev` | `states.amazonaws.com` | *(none attached yet)* | Step Functions orchestration |
| `ecom-lakehouse-eventbridge-sf-role-dev` | `events.amazonaws.com` | *(none attached yet)* | EventBridge → Step Functions trigger |
| `ecom-lakehouse-gha-deploy-role-dev` | GitHub OIDC (`token.actions.githubusercontent.com`) | **`AdministratorAccess`** | GitHub Actions CI/CD deployments |

**GitHub Actions trust condition:**
```
repo: eli-bigman/project-2-lakehouse
ref:  refs/heads/main
```
This matches ADR-013 (GitHub OIDC, no stored credentials) and the `main`-branch-scoped CI/CD constraint in CLAUDE.md.

**Notable:** `stepfunctions-role` and `eventbridge-sf-role` have no managed policies attached yet — inline policies may exist, or they may be waiting for Sprint 4 (Orchestration).

---

## 3. What the role CAN and CANNOT do

### Can (confirmed working)
| Action | Notes |
|--------|-------|
| `sts:GetCallerIdentity` | Always allowed |
| `iam:ListRoles` | Read all roles in account |
| `iam:GetRole` | Read role details (ARN, create date, trust policy) |
| `iam:ListAttachedRolePolicies` | Read attached managed policies |
| `iam:ListRoleTags` | Read role tags |
| `ce:GetCostAndUsage` | Read Cost Explorer data |

### Cannot (AccessDenied)
| Service | Blocked Actions |
|---------|----------------|
| S3 | `s3:ListAllMyBuckets` |
| Glue | `glue:GetJobs`, `glue:ListJobs`, `glue:GetDatabases` |
| Lambda | `lambda:ListFunctions` |
| Step Functions | `states:ListStateMachines` |
| DynamoDB | `dynamodb:ListTables` |
| Athena | `athena:ListWorkGroups` |
| KMS | `kms:ListKeys` |
| IAM self-inspection | `iam:GetRole` on own role, `iam:ListAttachedRolePolicies` on own role |

This means list-level access to the actual service resources is blocked under `DCEPrincipalDefaultPolicy-dce`. The **Terraform GHA deploy role** (`AdministratorAccess`) is what provisions them — you (DCEPrincipal) can read IAM structure but not enumerate service resources directly.

---

## 4. Cost visibility — services active this month

Total spend Jun 1–14 (estimated): **~$2.71**

| Service | Jun spend | May spend | Signal |
|---------|-----------|-----------|--------|
| AmazonCloudWatch | $0.52 | $1.15 | Running — log groups active |
| Amazon RDS | $0.48 | $30.65 | RDS was running heavily in May, now ~stopped |
| AWS KMS | $0.46 | $1.00 | KMS CMKs exist and are being used |
| Amazon ELB | $0.23 | $18.75 | Load balancer was running in May, minimal now |
| AWS Secrets Manager | $0.19 | $0.40 | Secrets in use |
| AWS Service Catalog | $0.15 | $3.39 | DCE lease management infrastructure |
| Amazon GuardDuty | $0.10 | $1.53 | GuardDuty enabled account-wide |
| **AWS Glue** | **$0.12** | **$0** | **Glue activity started in June — your infra** |
| **AWS Step Functions** | **~$0** | **~$0** | **Minimal executions (test runs?)** |
| **Amazon DynamoDB** | **~$0** | **~$0** | **DynamoDB table exists (watermark/ledger)** |
| Amazon S3 | $0.003 | $0.01 | S3 buckets exist, small storage |
| Amazon VPC | $0.04 | $3.72 | NAT gateway mostly off now |
| Tax | $0.42 | $11.57 | |

**Key insight:** Glue, Step Functions, DynamoDB, and S3 all show activity in June. The lakehouse infrastructure was bootstrapped yesterday (2026-06-14) and has already incurred some Glue and Step Functions costs — likely Terraform + test runs.

The large May charges (RDS $30, ELB $18, VPC $3.7) are from **other projects** run in this same DCE account before the current lease period.

---

## 5. Other account tenants (not your project)

The IAM role list reveals prior/other work in this account:

| Role group | Services |
|------------|---------|
| `sage-*` roles (7 roles) | A separate "Sage" project — Bedrock KB, API Gateway, Cognito, CloudFront, Firehose, Lambda |
| `AmplifySSRLoggingRole` (×2) | AWS Amplify web apps (created Mar 2026) |
| `ecsTaskExecutionRole`, `ecsCodeDeployRole` | ECS/CodeDeploy infrastructure |
| `felix-sns-role-*` | SNS work by another user |
| `todo-dynamo-role` | A todo-app DynamoDB setup |
| `cloudformationLabRole` | CloudFormation lab work |
| Elastic Beanstalk roles (×2) | Beanstalk apps (created Nov 2025) |

These are from **other students/projects** in the same shared DCE account. Not your concern, but worth knowing they exist.

---

## 6. Security posture observations

- `ecom-lakehouse-gha-deploy-role-dev` has **`AdministratorAccess`** — full account admin for CI/CD. This is broad; the ADRs don't specify a least-privilege policy for the GHA role. Acceptable for dev, worth tightening for prod.
- `GuardDuty` is active — threat detection is on.
- `SecurityHub` and `Inspector2` service roles exist — security scanning is enabled at account level.
- `AWS Config` is active (May had Config charges) — resource compliance tracking.
- `CloudTrail` is present (zero cost = within free tier, but logging is on).

---

## 7. What's likely deployed (inferred from cost + roles)

The roles + Glue/DynamoDB/S3/KMS costs together indicate:

- **S3 buckets** exist (raw, silver, gold zones + Terraform state)
- **KMS CMKs** exist (matching ADR-017 — CMK per bucket)
- **DynamoDB table** exists (ledger/watermark per ADR-006)
- **Glue** has been invoked (at least one job ran — $0.12 cost)
- **Step Functions** has had executions (tiny cost, likely test invocations)

What is **not yet visible** due to access restrictions: actual S3 bucket names, Glue job names, Lambda ARNs, Step Functions state machine name.

---

## 8. Recommended next steps

1. **Check `infra/`** — Terraform outputs will have the exact S3 bucket names, Glue job names, and DynamoDB table name.
2. **The GHA role is live** — pushing to `main` on `eli-bigman/project-2-lakehouse` will trigger the CI/CD deploy pipeline.
3. **Credentials expire** — re-run `.\scripts\set_aws_profile.ps1` at each session start.
4. **Monitor spend** — DCE leases have a budget cap. The heavy May RDS/ELB charges ($50+) came from other projects; your lakehouse June spend is ~$2.71 so far.
