# Senior Data Engineering Review: Deep-Dive Q&A

This document compiles advanced, senior-level data engineering questions and answers specifically tailored to the architectural trade-offs, Spark tuning, and cloud-native constraints of the `ecom-lakehouse` project. 

---

## Section 1: Distributed Computing & Spark Performance

### Q1: Tuning Spark Shuffle Partitions
**Question:** *Your Glue job runs with a dataset of only a few thousand rows. Spark's default `spark.sql.shuffle.partitions` is 200. Why is this default bad for this project, and how does it affect S3 I/O and performance?*

**Answer:** 
* **The Problem:** When performing wide transformations like `dedup()` (which uses window ranking) or `MERGE` (which triggers a join under the hood), Spark shuffles data across the network. By default, it splits the data into 200 shuffle partitions.
* **Impact on small data:** For our transaction volume, 200 partitions mean Spark creates 200 tiny tasks. Each task writes a physical file (or index) to disk/network. This creates a "small file problem" during shuffles and results in heavy CPU overhead just managing tasks rather than doing actual work.
* **Mitigation:** In our Spark configuration, we should set `spark.sql.shuffle.partitions` to a much lower number (e.g., `4` or `8`) for local/dev runs, matching our worker vCPU count. This forces Spark to consolidate tasks, dramatically reducing shuffle overhead and S3 API calls.

---

### Q2: Data Skew in Window Functions
**Question:** *Your `dedup()` logic uses `Window.partitionBy("order_id").orderBy(...)`. What happens if upstream systems bug out and send 10 million rows with the exact same `order_id` in a single batch? How does this cause Spark executors to crash with an OutOfMemory (OOM) error, and how do you resolve it?*

**Answer:** 
* **Why OOM occurs:** When Spark evaluates a Window function, it must move all rows sharing the same partition key (in this case, `order_id`) to the *same physical executor partition* in memory. If 10 million rows have the same ID, Spark cannot distribute them. One executor will attempt to load all 10 million rows into its heap, exceeding the JVM limits and throwing `java.lang.OutOfMemoryError: Java heap space`.
* **How to fix it:** 
  1. **Pre-aggregation:** If possible, perform a local map-side reduction or drop duplicate keys before applying the window.
  2. **Salting:** Append a random suffix (e.g., `_0`, `_1`) to the partitioning key, run a primary deduplication on the salted key, and then run a final deduplication on the unsalted key. This breaks up the massive skew across multiple executors.

---

### Q3: PySpark Column Evaluation assert
**Question:** *Why are validation rule predicates in `src/lakehouse/validation.py` wrapped in zero-argument lambdas instead of being evaluated directly at module import time?*

**Answer:** 
* **SparkContext Dependency:** PySpark column expressions (like `F.col("product_id")`) assert that a `SparkContext` is active.
* **Import Failures:** If we define validation rule dictionary entries directly as `F.col("id").isNotNull()` at the file's root level, importing `lakehouse.validation` during unit testing (before the test harness initializes the local Spark Session) will crash the Python interpreter with a `NullPointerException` or `SparkContext not initialized` error.
* **The Lambda Wrapper:** Wrapping them in lambdas (e.g., `lambda: F.col("product_id").isNotNull()`) delays their evaluation until they are explicitly called *inside* the active Glue Job or Spark unit test execution loop where the Spark session is running.

---

## Section 2: Storage & Open Table Format Architecture (Delta Lake)

### Q4: Write Amplification in Delta MERGE
**Question:** *Delta Lake uses a Copy-on-Write (CoW) mechanism by default. If your incoming batch updates a single row in `fct_orders`, does Delta write a single row to S3? Explain write amplification in this context.*

**Answer:** 
* **Copy-on-Write Mechanism:** No, it does not write a single row. S3 objects are immutable. Parquet files are written in blocks (usually 128MB). If a single row inside a 100MB Parquet file is updated, Delta Lake reads the entire original file, applies the update to that single row, and writes a *new* 100MB Parquet file to S3, marking the old file as tombstoned in the `_delta_log/` transaction log.
* **Write Amplification:** This represents a 1,000,000x write amplification. 
* **How we mitigate this:**
  1. **Partition Pruning:** We partition the target table by `order_date`. The `MERGE` query includes an explicit date range predicate (`target.order_date >= min_date AND target.order_date <= max_date`). This ensures Delta only scans and rewrites files within the active date partition, instead of scanning the entire historical table.
  2. **File Compaction (`OPTIMIZE`):** Compacts the tiny delta files written by frequent small merges into larger, optimized files.
  3. **Merge-on-Read (MoR):** In enterprise environments with high-frequency updates, we can enable Delta Lake's Merge-on-Read format (using Deletion Vectors). MoR writes only a small deletion file alongside new records, postponing the file rewriting step until the next compaction cycle.

---

### Q5: Optimistic Concurrency Control (OCC) and Concurrency Exceptions
**Question:** *What happens if two concurrent Step Functions attempt to run the Glue Ingest job and MERGE files into the same partition of `fct_orders` simultaneously? How does Delta Lake handle this conflict, and what is its transaction isolation level?*

**Answer:** 
* **OCC:** Delta Lake uses Optimistic Concurrency Control. Both jobs start by reading the current version (e.g., version 10) of the table. They perform writes in isolation, writing temporary Parquet files.
* **Commit Phase:** Job A commits first and creates `000011.json` in the transaction log. Job B then tries to commit version 11. Delta detects that Job B's read state is stale.
* **Conflict Resolution:** Delta analyzes if Job A's write overlaps with the files Job B read/wrote.
  * If they wrote to different partitions (e.g., different order dates), the transaction commits successfully (Delta automatically updates Job B's commit to version 12).
  * If they wrote to the *same* partition, Delta raises a `ConcurrentAppendException` or `WriteConflictException`, failing Job B's transaction.
* **Isolation Levels:** Delta Lake supports `WriteSerializable` (default) and `Serializable` isolation levels, protecting against dirty reads, non-repeatable reads, and phantom reads.

---

## Section 3: Cloud Infrastructure & Security

### Q6: Security & Role Separation (Glue vs. Lambda Archiving)
**Question:** *Why does the Glue job role (`ecom-lakehouse-glue-ingest-role-dev`) not have permissions to delete files from the Raw S3 bucket? What data engineering principle does this enforce?*

**Answer:** 
* **Principle of Least Privilege & Separation of Concerns:**
  * **Glue's Job:** Reads staging data, cleans it, and merges it into the DWH. It does not need to touch the Raw bucket. Giving it write/delete permissions on Raw introduces the risk of code bugs (e.g., recursive deletes) destroying raw data.
  * **Lambda's Job:** The `archive_file` Lambda is the only component with access to move raw files and delete them from the Raw landing zone. 
  * If the Spark job crashes, the raw file is left untouched in S3 Raw, allowing the operator to safely re-trigger the state machine without having to request the upstream systems to re-send the data.

---

### Q7: Athena Workgroups and Query Cost Control
**Question:** *You set `ATHENA_WORKGROUP = "primary"` (or a custom project workgroup). Why is it critical in a production environment to define dedicated Athena Workgroups, and what parameters do we configure there?*

**Answer:** 
* **Cost Allocation & Controls:** Athena queries are billed at $5 per TB of data scanned. If a junior developer runs `SELECT *` on a multi-TB table, it can incur massive costs.
* **Workgroup Controls:**
  1. **Data Scan Limits:** We can configure hard limits on the maximum amount of data scanned per query (e.g., 10 GB limit) or per hour across the workgroup.
  2. **Query Result Location:** Enforces where output results land in S3 (`ATHENA_RESULTS_URI`), separating query results of different departments.
  3. **Encryption Enforcements:** Forces all query results written to S3 to be encrypted at rest.
