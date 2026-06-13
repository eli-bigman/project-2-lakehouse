# infra/ — Terraform Infrastructure

E-Commerce Lakehouse on AWS. All infrastructure is declared as Terraform HCL in
reusable modules and composed in `envs/dev/` (and future `envs/prod/`).

---

## Quick start

### 1. Bootstrap (one-time — before `terraform init`)

The S3 backend bucket and DynamoDB lock table must exist before Terraform can
initialise. Create them manually with the `personal` AWS profile:

```bash
# State bucket
aws s3api create-bucket \
  --bucket ecom-lakehouse-tf-state-647594457599 \
  --region us-east-1 \
  --profile personal

aws s3api put-bucket-versioning \
  --bucket ecom-lakehouse-tf-state-647594457599 \
  --versioning-configuration Status=Enabled \
  --profile personal

aws s3api put-bucket-encryption \
  --bucket ecom-lakehouse-tf-state-647594457599 \
  --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"aws:kms"},"BucketKeyEnabled":true}]}' \
  --profile personal

# Lock table
aws dynamodb create-table \
  --table-name ecom-lakehouse-tf-locks \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region us-east-1 \
  --profile personal
```

### 2. Bootstrap the GitHub OIDC provider (one-time)

The IAM module references `data.aws_iam_openid_connect_provider.github`. Create it once:

```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1 \
  --profile personal
```

### 3. Variables

```bash
cd infra/envs/dev
cp dev.tfvars.example dev.tfvars
# Edit dev.tfvars — set account_id, alert_email, etc.
```

`dev.tfvars` is gitignored. Never commit real values.

### 4. Init / Plan / Apply

```bash
cd infra/envs/dev

# Set AWS profile (backend uses environment variable)
export AWS_PROFILE=personal

terraform init
terraform plan  -var-file=dev.tfvars
terraform apply -var-file=dev.tfvars
```

---

## Destroy / Teardown

### Stateless zones (safe to destroy anytime)

```bash
terraform destroy -target=module.stepfunctions -var-file=dev.tfvars
terraform destroy -target=module.lambda        -var-file=dev.tfvars
# etc.
```

### Stateful zones (raw, dwh, archive, quarantine)

**IMPORTANT:** These buckets have `lifecycle { prevent_destroy = true }` hardcoded
in `modules/s3_zones/stateful.tf`. This means `terraform destroy` will **always
error** on these resources, regardless of `protect_stateful`.

The `protect_stateful=false` flag enables `force_destroy` on the S3 bucket — this
tells S3 to empty the bucket before deleting it — but it does **not** bypass the
Terraform `prevent_destroy` lifecycle rule.

**Full teardown procedure for stateful buckets:**

1. Set `protect_stateful=false` in `dev.tfvars`.
2. Open `modules/s3_zones/stateful.tf` and comment out all four `lifecycle {
   prevent_destroy = true }` blocks.
3. Run `terraform apply -var-file=dev.tfvars` (updates `force_destroy`).
4. Run `terraform destroy -var-file=dev.tfvars` (or use `-target` per bucket).

This two-step process is intentional — it prevents accidental production data loss.

---

## Module descriptions

| Module | Purpose |
|--------|---------|
| `modules/s3_zones` | All 7 S3 buckets (raw, staging, dwh, archive, quarantine, athena-results, artifacts). Stateful buckets have `prevent_destroy`; stateless use `for_each`. |
| `modules/dynamodb` | Ingestion ledger + watermarks tables. PAY_PER_REQUEST, PITR enabled, KMS encrypted. |
| `modules/iam` | All IAM roles: normalize Lambda, Glue ingest, archive Lambda, Step Functions, GitHub Actions OIDC, EventBridge. Least-privilege inline policies. |
| `modules/glue` | Glue Data Catalog database, three Delta-registered tables (dim_products, fct_orders, fct_order_items), ingest + optimize Spark jobs (G.1X, 2 workers, auto-scaling off — ADR-020). |
| `modules/observability` | SNS alerts topic, email subscription, CloudWatch log group, Glue failure alarms, Step Functions failure alarm, dashboard. |
| `modules/lambda` | Four Lambda functions: normalize, claim_file, archive_file, validate_schema. Placeholder ZIP on first apply; CI/CD updates via S3. |
| `modules/stepfunctions` | Standard state machine (ADR-010), ASL definition from `asl.tftpl`, CloudWatch execution logs, EventBridge S3 trigger rule + target. |

---

## Known gaps and caveats

| Item | Detail |
|------|--------|
| **Athena workgroup** | Architecture §3.5 specifies `ecom_lakehouse_wg_{env}`. The Step Functions IAM policy references it, but no `aws_athena_workgroup` resource is declared in this module set (it was not in the file spec). Add it to `modules/glue/main.tf` or a dedicated `modules/athena/` module in Sprint 5. Until it exists, Athena queries by Step Functions will fall back to the primary workgroup. |
| **SNS + aws/sns managed key in dev** | When SNS uses `alias/aws/sns`, services like CloudWatch Alarms and EventBridge need `kms:GenerateDataKey`+`kms:Decrypt` — but the managed key policy cannot be edited. If alert delivery fails silently in dev, either (a) set `kms_key_arn` in `dev.tfvars` to a CMK, or (b) comment out `kms_master_key_id` in the SNS topic resource. |
| **Lambda placeholder ZIP** | First `terraform apply` deploys placeholder stubs. Real packages are uploaded by CI/CD via `aws lambda update-function-code`. The `source_code_hash` will drift from the S3 package; manage code updates outside Terraform (CI/CD pattern). |
| **Glue table StorageDescriptor** | Delta tables registered with `EXTERNAL_TABLE` + `parameters { table_type = "DELTA" }` is the correct pattern for Athena v3 native Delta reads (ADR-015). The `SequenceFileInputFormat`/`LazySimpleSerDe` values are Hive metadata placeholders — Athena ignores them when `table_type=DELTA` is set. Verify with the Athena Delta documentation after first ingest. |

---

## Key design decisions

| ADR | Impact on Terraform |
|-----|---------------------|
| ADR-005 | No S3 partitioning on Delta tables; Z-ORDER declared in Glue job scripts, not Terraform. |
| ADR-010 | `type = "STANDARD"` on state machine (Express ruled out by 5-min timeout). |
| ADR-013 | GitHub Actions OIDC role — no long-lived credentials stored in GitHub secrets. |
| ADR-015 | Glue table registered as `table_type = "DELTA"` — no symlink manifests, no MSCK REPAIR. |
| ADR-016 | No Glacier transitions in any lifecycle rule. |
| ADR-017 | `use_cmk = false` in dev (AWS-managed keys); set `use_cmk = true` + `kms_key_arn` in prod. |
| ADR-018 | Stateful S3 buckets declared as explicit resource blocks with `prevent_destroy = true`. |
| ADR-019 | Glue jobs use `--datalake-formats=delta`; DynamicFrames are never configured. |
| ADR-020 | `worker_type = "G.1X"`, `number_of_workers = 2`, no `max_capacity`. |
| ADR-021 | `EnforceHTTPS` + `EnforceKMSEncryption` Deny statements on every data bucket policy. |
