# E-Commerce Lakehouse: Backend & Data Engineering Defense Guide

This guide is prepared to help you defend the **data engineering and PySpark backend logic** of the E-Commerce Lakehouse project.

---

## 1. Medallion Architecture Layer
The project processes transactional data (Products, Orders, and Order Items) from raw ingestion to the data warehouse using the Medallion Pattern:

* **Raw Zone (Bronze):** CSV/Excel files land here from source systems. They are treated as immutable, write-once records.
* **Staging Zone (Silver Parquet):** Raw files are normalized to columnar Parquet format to enable fast reading and schema validation.
* **DWH Zone (Gold Delta Lake):** Valid, deduplicated, and typed data is merged into Delta tables. Fact tables (`fct_orders`, `fct_order_items`) are partitioned by date.
* **Quarantine Zone:** Corrupt rows failing validation are written here as Parquet with a `rejection_reason` column.
* **Archive Zone:** Raw files are moved here after successful loading.

---

## 2. Dynamic Schema Validation & Data Quality

### A. Format Normalization & Validation (Fail-Fast)
We run format normalization and schema checking inside lightweight **AWS Lambda functions** before running **AWS Glue (PySpark)**.
* **Why?** Glue has a cold start latency (around 30-60s) and is billed based on Spark compute workers. Lambdas spin up in milliseconds and cost a fraction of the price. If a file has invalid column names or incorrect datatypes, we fail the pipeline at the Lambda stage before starting Glue.
* **Logic:** The `validate_schema` Lambda reads the schema from `lakehouse.schemas` and compares the columns of the staging Parquet file against the canonical JSON schema.

### B. Inline Row-Level Quarantine Strategy
If a schema matches but some rows contain dirty records (e.g., null primary keys or invalid IDs):
1. In PySpark, columns are cast to their canonical types. Any required column that fails to cast sets a `_cast_failed` flag.
2. In `lakehouse/validation.py`, we apply rules:
   ```python
   # Example: PK Null Check
   valid_pk = F.col(pk_column).isNotNull()
   # Referential Integrity check (e.g., product_id in order_items exists in dim_products)
   ```
3. PySpark splits the DataFrame:
   * **Clean DataFrame:** Rows where `_cast_failed` is false and all rules pass.
   * **Quarantine DataFrame:** Rows where any rule or cast fails. We append a `rejection_reason` string (e.g., `"[NULL_PK]"` or `"[CAST_FAILED]"`) to these rows and write them to S3 Quarantine.
   * **Result:** The pipeline is non-blocking. Clean data is loaded immediately, while dirty data is isolated for debugging.

---

## 3. How the Duplicate Process is Checked (Crucial Defense Topic)

Our architecture handles duplicates at two distinct layers: **File Ingestion (Idempotency)** and **Record-Level Upsert (Deduplication)**.

### Layer A: File-Level Ingestion Check (DynamoDB Ledger)
To prevent duplicate processing of the same file (e.g., if a file is uploaded twice or if the pipeline is retried), we use the `claim_file` Lambda and DynamoDB.

1. **MD5 Checksum:** We compute the MD5 hash of the raw file in the `claim_file` Lambda:
   ```python
   response = s3_client.get_object(Bucket=raw_bucket, Key=file_key)
   md5 = hashlib.md5()
   for chunk in response["Body"].iter_chunks(chunk_size=8 * 1024 * 1024):
       md5.update(chunk)
   checksum = md5.hexdigest()
   ```
2. **Conditional Put (Idempotency Lock):** We attempt to write this to DynamoDB using a conditional statement:
   ```python
   self.ledger_table.put_item(
       Item=item,
       ConditionExpression=("attribute_not_exists(file_key) OR #s = :failed"),
       ExpressionAttributeNames={"#s": "status"},
       ExpressionAttributeValues={":failed": "FAILED"},
   )
   ```
3. **Outcome:**
   * If the file has **never** been seen, or if its previous status is **FAILED**, the write succeeds, setting status to `CLAIMED`.
   * If the file is currently processing or completed (`CLAIMED`, `NORMALIZED`, `LOADED`, `ARCHIVED`), DynamoDB throws a `ConditionalCheckFailedException`. The Lambda catches this, returns `already_processed=True`, and Step Functions skips the rest of the pipeline.

---

### Layer B: Record-Level Deduplication (PySpark)
If a single batch contains multiple rows for the same entity (e.g., two order updates for the same `order_id` in one file), we perform **within-batch deduplication** before merging.

1. **Window Ranking (`transforms.py`):** We partition by the primary key and order by the ingestion timestamp (`_ingest_ts`) and record content hash (`_record_hash`) descending:
   ```python
   window_spec = Window.partitionBy(merge_key).orderBy(
       F.col("_ingest_ts").desc(),      # primary: latest timestamp wins
       F.col("_record_hash").desc(),    # secondary: deterministic tie-breaker
   )
   df_ranked = df.withColumn("_dedup_rn", F.row_number().over(window_spec))
   df_deduped = df_ranked.filter(F.col("_dedup_rn") == 1).drop("_dedup_rn")
   ```
   * *Defense Tip:* Explain why we use `row_number()` instead of `rank()`. `rank()` yields duplicate ranks (e.g., two rows getting rank `1` if timestamps tie), which will crash the Delta table merge. `row_number()` always assigns a unique sequential number, ensuring exactly one row wins.

2. **Cross-Batch Upsert (Delta Lake MERGE):**
   * Once within-batch duplicates are cleared, we upsert into the target Delta table:
     ```python
     delta_table.alias("target")
         .merge(df.alias("source"), "target.order_id = source.order_id")
         .whenMatchedUpdateAll(condition="source._record_hash != target._record_hash")
         .whenNotMatchedInsertAll()
         .execute()
     ```
   * **Hash Comparison Optimization:** We compute `_record_hash` (a SHA-256 of all business columns) on each row. The condition `source._record_hash != target._record_hash` ensures that if a record is identical to the one already in the database, Delta Lake **skips writing it**. This reduces write amplification, saves S3 storage costs, and reduces Delta log sizes.

---

## 4. Why Delta Lake?

If the reviewer asks why you used **Delta Lake** instead of Apache Iceberg or plain Parquet files:
* **ACID Transactions:** S3 is eventually consistent. Writing raw Parquet concurrently or in case of job failures leaves half-written files. Delta Lake writes a transaction log (`_delta_log/`), making all writes atomic.
* **In-place Updates:** Support for `MERGE INTO` is native in Delta, which is required for e-commerce updates (e.g., changes to order statuses or details).
* **Time Travel:** Enables reading snapshots at specific transaction versions for auditing or debugging.
* **Why not Apache Iceberg?** Delta Lake has first-class native integration with Spark (built by Databricks) and works seamlessly with AWS Glue and Amazon Athena via Glue Catalog without needing separate catalog servers or manifest configurations.

---

## 5. Performance Optimizations
Explain how you optimized query times and storage costs:
1. **Partitioning:** Fact tables are partitioned by `order_date`. Athena queries filtering on date will only scan files in the matching partition directories, saving query cost.
2. **OPTIMIZE Z-ORDER:** After merging, we run `OPTIMIZE ZORDER BY (col)` which compacts small merge files and groups records together by columns like `product_id` to maximize file skipping statistics.
3. **VACUUM:** We run `VACUUM` with a 7-day retention window to delete deprecated physical Parquet files from history, cleaning up storage space.
