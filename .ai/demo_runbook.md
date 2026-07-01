# E-Commerce Lakehouse: Live Demo Runbook & Script

This document provides a step-by-step script for conducting a live demonstration of the e-commerce data lakehouse platform for reviewers or interviewers.

---

## 1. Demo Preparation (Clean Start)

Ensure your terminal session has active AWS credentials configured:
```powershell
# In PowerShell:
$env:AWS_PROFILE = "sandbox-lakehouse-dev"
$env:AWS_REGION = "eu-west-1"
```

### Automated One-Command Spin-Up (Recommended)
You can build, deploy, clean, seed, and poll the entire platform in a single command using the automated orchestrator. This handles Terraform apply, Lambda deployment, PySpark packaging, S3 uploads, database reset, and polls the pipeline executions until all tables are successfully populated:
```powershell
make demo-up
```
*This takes ~4 minutes to complete. It outputs a status report showing the exact execution status of all ingestion runs. Alternatively, you can use `python scripts/demo_spinup.py` directly.*

### Manual Step-by-Step Spin-Up (Under the Hood)
If the reviewer asks how the deployment works, you can explain that the automated script executes the following stages sequentially:

1. **Provision Infrastructure**: Run Terraform to create resources:
   ```powershell
   cd infra/envs/dev
   terraform init
   terraform apply -var-file=dev.tfvars -auto-approve
   cd ../../..
   ```
2. **Package & Deploy Spark Code**: Build the PySpark library wheel and copy Glue jobs to the S3 artifacts bucket:
   ```powershell
   # Build wheel and stage scripts
   pip wheel --no-deps -w dist/ .
   aws s3 cp dist/ecom_lakehouse-0.1.0-py3-none-any.whl s3://ecom-lakehouse-artifacts-dev/wheels/lakehouse-latest.whl --sse aws:kms
   aws s3 cp src/glue_jobs/ingest.py s3://ecom-lakehouse-artifacts-dev/scripts/ingest.py --sse aws:kms
   aws s3 cp src/glue_jobs/optimize.py s3://ecom-lakehouse-artifacts-dev/scripts/optimize.py --sse aws:kms
   ```
3. **Deploy Lambda Code**: Zip and deploy handlers to AWS Lambda directly:
   ```powershell
   python scripts/deploy_lambdas.py
   ```


---

## 2. Live Demo Script (Step-by-Step)

### Phase 1: Day Zero Sandbox (Empty State)
1. **AWS Console S3:** Open S3 and show that your raw bucket (`ecom-lakehouse-raw-dev`) is empty.
2. **AWS Console DynamoDB:** Open DynamoDB and show that the `ecom_lakehouse_ingestion_ledger_dev` table is empty.
3. **Athena Console:** Show that the database `ecom_lakehouse_db_dev` exists (from the Glue Catalog) but has no tables or records queryable.
4. **Talking Point:** *"We are starting from a completely clean state. The raw ingestion zone has no files, the metadata control plane is unpopulated, and Athena is empty."*

### Phase 2: Ingest the Datasets (Manual Upload)
Upload the raw dimension and fact files from your `Data/` folder to the raw bucket:
```powershell
# 1. Products (CSV)
aws s3 cp Data/products.csv `
  s3://ecom-lakehouse-raw-dev/dim_products/2025/04/products.csv `
  --sse aws:kms

# 2. Orders (XLSX)
aws s3 cp "Data/orders_apr_2025.xlsx" `
  s3://ecom-lakehouse-raw-dev/fct_orders/2025/04/orders_apr_2025.xlsx `
  --sse aws:kms

# 3. Order Items (XLSX)
aws s3 cp "Data/order_items_apr_2025.xlsx" `
  s3://ecom-lakehouse-raw-dev/fct_order_items/2025/04/order_items_apr_2025.xlsx `
  --sse aws:kms
```
* **Talking Point:** *"We enforce strict KMS encryption at rest. S3 bucket policies are configured to explicitly deny any PutObject calls that do not include the KMS encryption header (`aws:kms`). This ensures data security by default."*

### Phase 3: The Orchestration Graph Walkthrough
* The uploads will trigger the EventBridge rule automatically, starting three separate Step Functions runs (one per dataset).
* Open the **AWS Step Functions console**, click on the active execution for `orders` or `order_items`, and display the **Graph View**.
* **Walking through the Nodes:**
  * **`claim_file` (Lambda):** *"This computes the file's MD5 checksum and registers the file in DynamoDB with a `CLAIMED` status. If this execution is a duplicate, the conditional lock fails, and the run exits immediately (idempotency)."*
  * **`normalize_to_parquet` (Lambda):** *"Excel `.xlsx` formats are not natively readable by Spark without fragile third-party Jars. Rather than booting up an expensive Glue cluster to read Excel, this lightweight Lambda converts the sheets to Parquet for fractions of a cent, saving us compute costs."*
  * **`validate_schema` (Lambda):** *"This checks column counts, names, and data types. If the schema is corrupted, we fail the execution right here (fail-fast) before incurring Spark startup charges."*
  * **`Glue Ingest Job` (PySpark Spark Session):** *"Now the serverless Glue job runs. It reads the staging Parquet, filters out corrupt rows (routing them to Quarantine S3), and executes an atomic Delta Lake `MERGE` into S3 on the primary keys."*
  * **`archive_file` (Lambda):** *"Once Glue completes, this Lambda moves the raw landing file to an Archive bucket for compliance, deletes it from Raw, and marks the ledger status to `ARCHIVED`."*

### Phase 4: Proving Exactly-Once & Idempotency
1. Go back to your terminal and run the upload command for `orders_apr_2025.xlsx` again:
   ```powershell
   aws s3 cp "Data/orders_apr_2025.xlsx" `
     s3://ecom-lakehouse-raw-dev/fct_orders/2025/04/orders_apr_2025.xlsx `
     --sse aws:kms
   ```
2. Open the Step Functions execution console for the new run.
3. Show the reviewer that the pipeline **exits immediately** at the `Choice` step.
4. **Talking Point:** *"Here is the exactly-once processing guarantee. Even if the same file is uploaded multiple times, our DynamoDB ledger detects the duplicate MD5 checksum/path and halts, preventing duplicate loads."*

### Phase 5: Audit & Validation (Athena Queries)
1. **Show DynamoDB Ledger:** Open the DynamoDB console and scan the ledger table. Point out that the ledger status is `ARCHIVED` and has stored the metrics: `raw_count = 500`, `clean_count = 500`, `reject_count = 0`.
2. **Show Quarantine Bucket:** Open the S3 quarantine bucket and show any rejected rows (especially for `order_items` which contains invalid product keys).
3. **Query in Athena:** Open the Athena console and run:
   ```sql
   -- Verify dimension and fact tables exist and count rows
   SELECT COUNT(*) FROM ecom_lakehouse_db_dev.dim_products; -- Expects 1,000
   SELECT COUNT(*) FROM ecom_lakehouse_db_dev.fct_orders;   -- Expects 500
   SELECT COUNT(*) FROM ecom_lakehouse_db_dev.fct_order_items; -- Expects 2,768
   ```
4. **Talking Point:** *"Athena queries S3 Delta tables directly via metadata stored in the Glue Data Catalog. Because Delta tables write a transaction log, readers see an atomic snapshot of the data, eliminating partial or dirty reads."*

---

## 3. Post-Demo Cleanup

### Option A: Reset (Keep Infrastructure, Reset Data)
If you want to reset the database and buckets to day-zero for another practice run without deleting your AWS infrastructure:
```powershell
python scripts/clean_slate.py
```
*This empties all S3 buckets (including all old file versions) and wipes all items from your DynamoDB tables.*

### Option B: Automated One-Command Tear Down (Recommended)
To destroy all AWS infrastructure and clean up your sandbox with a single command (which automatically empties S3 buckets with versioning enabled and overrides stateful protection guards):
```powershell
make demo-down
```
*Alternatively, you can run `python scripts/demo_teardown.py` directly.*

### Option C: Manual Step-by-Step Tear Down (Under the Hood)
If doing it manually, you must first empty the versioned S3 buckets, then run terraform destroy with `protect_stateful` set to `false`:
```powershell
# 1. Empty raw/staging/dwh/archive/quarantine/athena-results/artifacts buckets
# (Deletes all object versions and delete markers to prevent bucket deletion block)

# 2. Destroy infrastructure via Terraform
cd infra/envs/dev
terraform destroy -var-file=dev.tfvars -var="protect_stateful=false" -auto-approve
cd ../../..
```

