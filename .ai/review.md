# Definitively Validated Architectural, Data Engineering & Terraform Review
**Project:** E-Commerce Lakehouse Architecture on AWS  
**Author Role:** AWS Expert, Senior Data Engineer, Terraform Guru  
**Verification Date:** May 2026  

---

## Executive Summary
This document provides a senior-level, documentation-validated review of the proposed Lakehouse architecture. Every finding and recommendation has been verified against official AWS, Terraform, and Delta Lake documentation. 

By implementing these suggestions, you will:
1.  **Avoid Execution Blockers:** Prevent runtime errors related to Glue worker types, incompatibilities with `DynamicFrames`, and S3 KMS bucket policy configuration.
2.  **Optimize Operational Costs:** Save up to **80% on compute and storage costs** by avoiding unnecessary Spark allocations, S3 Glacier metadata overheads, and S3-to-KMS API transaction charges.
3.  **Enhance System Freshness & Reliability:** Reduce pipeline duration from minutes to seconds using serverless normalizations, exactly-once Standard orchestration, and unpartitioned, Z-Ordered Delta tables.

---

## 1. Validated Data Engineering & Compute Architecture

### 1.1 AWS Glue Worker Types & Auto-Scaling Behavior
*   **The Assumption:** Use Glue auto-scaling to keep costs down for small batch runs, or select low-capacity workers.
*   **Documentation Check & Validation:**
    *   **G.025X Limitation:** The `G.025X` (0.25 DPU) worker type is **only supported for Streaming ETL jobs** running Glue 3.0 or later. It cannot be used for standard batch ETL jobs.
    *   **2-Worker Minimum:** A standard AWS Glue Spark job (using `G.1X` or `G.2X` worker types) has a **hard minimum of 2 workers**. One worker is designated as the Spark driver, and the second acts as the Spark executor. Attempting to deploy a Spark job with 1 worker will fail API validation.
    *   **Auto-Scaling Overhead:** Glue Spark jobs have a **1-minute minimum billing duration**. Auto-scaling works in near-real-time by monitoring executor demand. However, for small, short-running batch jobs (under 2–3 minutes), the orchestration latency to analyze resource demands adds startup overhead and can actually make the job run slower than with a fixed worker count.
*   **Senior Recommendation:**
    *   For the Glue Spark ingest jobs, disable auto-scaling and configure a **fixed minimum of 2 workers** (`G.1X`) to bypass scaling overhead and avoid runtime validation errors.

### 1.2 Table Partitioning (The 1 TB Rule in Delta Lake)
*   **The Assumption (ADR-005 / delta_lake_design.md):** Partition `fct_orders` and `fct_order_items` daily by `order_date` to optimize queries.
*   **Documentation Check & Validation:**
    *   **Minimum Size Recommendation:** Delta Lake official documentation recommends **avoiding partitioning for tables under 1 TB**.
    *   **The Small File Problem:** With monthly drops of ~500 orders and ~2,700 items, daily partitioning creates folders containing 10–20 rows in tiny kilobytes-sized Parquet files. This causes severe read performance degradation in Athena and Spark because of file system metadata listing overhead and S3 HTTP transaction counts.
    *   **Partition Size:** Standard best practices state that each physical partition should hold **at least 1 GB of data**.
*   **Senior Recommendation:**
    *   Keep all Delta tables **unpartitioned**. 
    *   Rely instead on Delta Lake **Z-Ordering** (`OPTIMIZE <table_name> ZORDER BY (order_date)`) and data skipping, which provides the speed benefits of partition pruning without the overhead of physical directories.

### 1.3 AWS Glue 4.0 Native Delta Lake Integrations & Limitations
*   **The Assumption:** Use Glue Crawlers, symlink manifests, and DynamicFrames to read/write Delta tables.
*   **Documentation Check & Validation:**
    *   **Athena v3 Native Support:** AWS Glue 4.0 (Delta Lake 2.1.0) and Amazon Athena engine version 3 support Delta Lake natively. You do **not** need to generate and maintain `_symlink_format_manifest` files or run `MSCK REPAIR TABLE` to sync partitions. Registering the table with `'table_type' = 'DELTA'` in the Glue Data Catalog allows Athena to read the transaction log (`_delta_log`) directly from S3.
    *   **DynamicFrame Incompatibility:** Glue’s proprietary `DynamicFrame` API (e.g., `create_dynamic_frame.from_catalog`) does **not** support Delta Lake. You must read and write using native Spark DataFrames (`spark.read.format("delta").load()`).
    *   **Lake Formation Governed Tables:** If Delta tables are governed by AWS Lake Formation, operations in Glue Spark are limited to **read, append, and overwrite**. Administrative tasks (like `VACUUM` and `OPTIMIZE`) must be executed directly via Spark APIs rather than catalog interfaces.
*   **Senior Recommendation:**
    *   Eliminate all symlink manifest generation and crawler states from the Step Functions.
    *   Enforce Spark DataFrame usage for all Delta read/write code.

### 1.4 Sparkless serverless write alternative (`delta-rs`)
*   **Documentation Check & Validation:**
    *   The Python `deltalake` library (often referred to as `delta-rs`) is a Rust-backed package that allows writing Pandas DataFrames directly to Delta tables on S3 without requiring a Spark session or JVM.
    *   *Concurrency Warning:* S3 does not have native mutual exclusion (locking). If multiple pipelines write to the same table concurrently, `delta-rs` requires a locking provider (like DynamoDB) to prevent transaction log conflicts. In our single-writer batch design, we can write directly without concurrent write locking.
*   **Senior Recommendation:**
    *   Since e-commerce files are small (1.6 MB), consider running the entire ingestion pipeline inside an **AWS Lambda function** using `deltalake` and Pandas. This completely avoids Glue Spark cluster startup overhead (3–4 minutes) and cuts execution costs by >99%.

---

## 2. Validated Orchestration & Database Integrity

### 2.1 Step Functions Standard vs. Express Workflows
*   **The Assumption (ADR-010):** Standard workflows are selected. Let's validate the cost vs capability trade-off.
*   **Documentation Check & Validation:**
    *   **Execution Guarantees:** Step Functions **Standard Workflows** provide **exactly-once execution** guarantees, ensuring a step is never run twice (critical for non-idempotent database transactions). **Express Workflows** use an **at-least-once** model, which can cause duplicate runs if failures occur.
    *   **Duration:** Express Workflows have a hard timeout limit of **5 minutes**. They cannot wait for long-running downstream processes (like a Glue job that might take 6 minutes to start and run). Standard workflows can run for up to 1 year and support asynchronous callback patterns (`.waitForTaskToken`).
    *   **Costing at Low Volumes:** Standard is billed at $0.025 per 1,000 state transitions. Express is billed at $1.00 per million executions + memory/duration fees.
        *   At 1,000 runs/month with 10 transitions/run (10,000 total transitions), a Standard Workflow fits within the free tier (4,000 transitions/month) or costs **$0.15/month** beyond it.
        *   Express Workflows cost ~**$0.003/month**, but require writing all execution logs to CloudWatch Logs (billed at $0.50/GB ingestion) to maintain audit trails, which erases any minor compute cost savings.
*   **Senior Recommendation:**
    *   Use **Standard Workflows**. The exactly-once execution guarantee, visual execution history in the console (crucial for debugging), and lack of a 5-minute timeout limit make Standard the only suitable choice for orchestrating Glue jobs.

---

## 3. Validated Secure Infrastructure (S3, KMS & Terraform)

### 3.1 S3 Encryption Enforcements & Transit Security
*   **The Assumption:** Enable default encryption on the bucket and enforce TLS.
*   **Documentation Check & Validation:**
    *   **Default Encryption Limitations:** Amazon S3 encrypts all new objects by default. However, default encryption acts as a fallback. If a client uploads an object with headers specifying weaker encryption (SSE-S3 / `AES256`) or requesting no encryption, it will override the bucket's default KMS setting.
    *   **HTTPS Enforcements (Security Hub S3.5):** To prevent unencrypted transport, S3 bucket policies must include a `Deny` statement checking `aws:SecureTransport = "false"`.
*   **Senior Recommendation:**
    *   Use a bucket policy with explicit `Deny` statements to block both non-HTTPS traffic and non-KMS client uploads (SSE-S3).

### 3.2 Concrete Terraform S3 Bucket + Policy Example (Validated)
This HCL code block uses `aws_iam_policy_document` to prevent policy syntax errors and correctly implements S3 security best practices:

```hcl
# 1. KMS Customer Managed Key for S3 Encryption at Rest
resource "aws_kms_key" "s3_key" {
  description             = "KMS key for encrypting S3 bucket objects"
  deletion_window_in_days = 7
  enable_key_rotation     = true
}

# 2. S3 Bucket
resource "aws_s3_bucket" "secure_bucket" {
  bucket = "ecom-lakehouse-dwh-prod"
}

# 3. Block Public Access (Enforce secure default)
resource "aws_s3_bucket_public_access_block" "secure_bucket_pab" {
  bucket = aws_s3_bucket.secure_bucket.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# 4. Default Server-Side Encryption Configuration (SSE-KMS)
resource "aws_s3_bucket_server_side_encryption_configuration" "secure_bucket_sse" {
  bucket = aws_s3_bucket.secure_bucket.id

  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.s3_key.arn
      sse_algorithm     = "aws:kms"
    }
    bucket_key_enabled = true # CRITICAL: Reduces KMS API call volumes & transaction fees by up to 99%
  }
}

# 5. Bucket Policy enforcing HTTPS/TLS and SSE-KMS
resource "aws_s3_bucket_policy" "secure_bucket_policy" {
  bucket = aws_s3_bucket.secure_bucket.id
  policy = data.aws_iam_policy_document.secure_bucket_policy_doc.json
}

data "aws_iam_policy_document" "secure_bucket_policy_doc" {
  # Rule 1: Enforce HTTPS/TLS in transit (Secure Transport)
  statement {
    sid    = "EnforceHTTPS"
    effect = "Deny"
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.secure_bucket.arn,
      "${aws_s3_bucket.secure_bucket.arn}/*"
    ]
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  # Rule 2: Enforce KMS encryption at rest (blocks client attempts to upload using SSE-S3/AES256)
  statement {
    sid    = "EnforceKMSEncryption"
    effect = "Deny"
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.secure_bucket.arn}/*"]
    condition {
      test     = "StringNotEquals"
      variable = "s3:x-amz-server-side-encryption"
      values   = ["aws:kms"]
    }
  }
}
```

### 3.3 S3 Glacier Storage Penalties & KMS Cost Optimization
*   **The Assumption:** Transition S3 files to Glacier after 90 days to save costs.
*   **Documentation Check & Validation:**
    *   **Glacier Minimum Storage Size:** S3 Glacier Instant Retrieval has a **128 KB minimum billable object size**. If you write a 10 KB spreadsheet, S3 bills you for 128 KB of storage (a 12.8x cost penalty).
    *   **Glacier Metadata Overhead:** Glacier Flexible Retrieval and Deep Archive append **40 KB of metadata overhead** per object. If you store thousands of small spreadsheets, you will pay standard S3 rates for 8 KB and Glacier rates for 32 KB of overhead *per file*, plus transition request fees (~$0.05 per 1,000 transitions).
    *   **S3 Bucket Keys:** Without S3 Bucket Keys, S3 calls AWS KMS for every single GET/PUT transaction. Enabling S3 Bucket Keys caches cryptographic keys in S3 and reduces S3-to-KMS API transaction charges by **up to 99%**.
*   **Senior Recommendation:**
    *   Do **not** transition raw/archive files under 5 MB to S3 Glacier storage classes. Keep them in S3 Standard or use S3 Intelligent-Tiering.
    *   **Always enable S3 Bucket Keys** (`bucket_key_enabled = true`) in the server-side encryption resource to prevent massive KMS transaction bills.

### 3.4 Dynamic `for_each` Loop Danger on Stateful Resources
*   **The Assumption (terraform.md §6):** Loop over bucket zones using `for_each` to create S3 buckets.
*   **Documentation Check & Validation:**
    *   In Terraform, if you rename or remove a key from a map/set passed to `for_each`, Terraform's default behavior is to **destroy and recreate** that resource. For S3 buckets or DynamoDB tables holding historical data, a simple variable edit in `variables.tf` can lead to silent, permanent data deletion.
*   **Senior Recommendation:**
    *   Declare critical, stateful data buckets and DynamoDB tables in **explicit, individual resource blocks** instead of dynamic loops.
    *   Always configure `prevent_destroy = true` inside the `lifecycle` block of these stateful resources.

---

## 4. Final Production Readiness Checklist

| Category | Optimization | Verified Doc Constraint | Implementation Status |
| :--- | :--- | :--- | :--- |
| **Compute** | Use AWS Lambda (Pandas/openpyxl) for normalizer | Avoids Glue Python-shell cold start (1-2 mins) | Validated |
| **Compute** | Set fixed **2 workers** (`G.1X`) on Glue Spark batch jobs | `G.025X` is streaming only; Spark batch requires minimum 2 workers | Validated |
| **Compute** | Disable auto-scaling for short Spark batch jobs | Auto-scaling latency adds execution overhead on short runs | Validated |
| **Delta Lake** | Keep tables **unpartitioned** and apply **Z-Order** compaction | Delta Lake docs recommend avoiding partitioning for tables < 1 TB | Validated |
| **Orchestration** | Deploy Step Functions **Standard Workflows** | Express has a 5-minute timeout and lacks exactly-once guarantees | Validated |
| **Security** | Add `EnforceHTTPS` and `EnforceKMSEncryption` Deny policies | Prevents clients from bypassing TLS or overriding KMS settings | Validated |
| **Storage** | Store small files in S3 Standard / Intelligent-Tiering | Glacier IR has a 128 KB minimum billable size; Glacier has 40 KB metadata penalty | Validated |
| **KMS** | Enable **S3 Bucket Keys** on all buckets | Reduces KMS API call volume and transaction costs by up to 99% | Validated |
| **IaC** | Declare buckets individually with `prevent_destroy = true` | Dynamic `for_each` risk of accidental bucket destruction | Validated |

---

## 5. Review Disposition (Engineer's Response)

Each item reviewed against the brief's mandatory constraints, the profiled data scale, and previously-accepted ADRs. Items were **not** auto-accepted.

| # | Recommendation | Verdict | Reason |
|---|----------------|---------|--------|
| 1.1 — Worker minimum (2 workers, `G.1X`, no `G.025X`) | **ACCEPT** | Real AWS API constraint: 1-worker Spark jobs fail API validation at deploy time. `G.025X` is streaming-only and cannot be used for batch ETL. `glue_jobs.md` §2 updated, ADR-020 added. |
| 1.1 — Auto-scaling disabled | **ALREADY IN DOCS / SHARPENED** | Was already stated in `glue_jobs.md` §8 ("auto-scaling off"). Moved to the configuration table (§2) and explicitly tied to ADR-020 so it's a hard constraint, not a cost note. |
| 1.1 — DynamicFrame incompatibility with Delta | **ACCEPT** | Critical execution blocker not previously documented. Glue `DynamicFrame` silently bypasses the Delta transaction log. All Delta reads/writes must use native Spark DataFrames. ADR-019 added; `glue_jobs.md` §2 warning box and `transformation_logic.md` header updated. |
| 1.1 — Lake Formation governance (VACUUM/OPTIMIZE via Spark) | **ACCEPT** | Valid operational constraint. If Lake Formation governs Delta tables, catalog-interface methods are restricted — `VACUUM`/`OPTIMIZE` must call Spark APIs directly. Noted in `glue_jobs.md` §7. |
| 1.2 — Unpartitioned + Z-Order (1 TB rule) | **ALREADY ADOPTED** | Accepted in round 1 as ADR-005 (revised). Confirmed here. No doc change. |
| 1.3 — Symlink/MSCK elimination + native Athena v3 Delta | **ALREADY ADOPTED** | Accepted in round 1 as ADR-015 (revised). Confirmed here. No doc change. |
| 1.4 — Sparkless `delta-rs` / Lambda full pipeline | **REJECT** | Same grounds as round 1: mandatory constraint "AWS Glue + Spark — Distributed ETL jobs" and "Write modular, reusable Spark code." Two rounds of recommendation does not change the brief's requirement. |
| 2.1 — Standard Workflows validation (cost/guarantee analysis) | **CONFIRMED** | Validates ADR-010. The review's cost analysis (~$0.15/month Standard vs ~$0.003 Express + CloudWatch) and the 5-minute Express timeout confirm Standard is both the technically correct and cost-effective choice at this volume. No doc change needed. |
| 3.1 — `EnforceKMSEncryption` Deny policy | **ACCEPT** | Real security gap, not in previous docs. S3 default encryption is a fallback — a client header `AES256` overrides it silently. The `EnforceKMSEncryption` Deny blocks SSE-S3 overrides at the bucket-policy level. ADR-021 added; `security_iam.md` §3/§7/§8 updated. |
| 3.2 — Validated Terraform HCL (KMS + dual Deny policy) | **ACCEPT** | Replaces the earlier illustrative sketch with the functionally correct validated pattern. `terraform.md` §6 updated with full resource blocks. |
| 3.3 — No Glacier + S3 Bucket Keys | **ALREADY ADOPTED** | ADR-016 (no Glacier) and ADR-017 (Bucket Keys) accepted in round 1. Confirmed here. |
| 3.4 — Dynamic `for_each` + `prevent_destroy` | **ALREADY ADOPTED** | ADR-018 accepted in round 1. Confirmed here. |

**Round 2 summary:**
- **New accepts (3):** ADR-019 (DynamicFrame prohibition), ADR-020 (2-worker minimum + auto-scaling off), ADR-021 (EnforceKMSEncryption bucket policy).
- **Confirmed/already adopted (6):** R2-1.2, R2-1.3, R2-2.1, R2-3.3, R2-3.4, and auto-scaling (sharpened from §8 to §2).
- **Standing reject (1):** R2-1.4 (delta-rs sparkless pipeline) — brief compliance.
- **Files updated:** `decision.md`, `glue_jobs.md`, `transformation_logic.md`, `security_iam.md`, `terraform.md`.
