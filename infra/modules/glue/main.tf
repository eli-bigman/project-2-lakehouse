# FILE 9: infra/modules/glue/main.tf
# Glue Data Catalog database, Delta-registered tables, and Glue Spark jobs.
# ADR-015: Athena v3 reads Delta natively — no symlink manifests, no MSCK REPAIR.
# ADR-019: DynamicFrames prohibited; Spark DataFrames + Delta extensions only.
# ADR-020: G.1X workers, NumberOfWorkers=2, auto-scaling disabled.
# Schemas from Architecture Design Contract §3.3.

locals {
  # Resolved database name: use override if provided, otherwise use convention.
  db_name = var.database_name != "" ? var.database_name : "ecom_lakehouse_db_${var.env}"

  # Common Glue job arguments shared across all jobs.
  common_glue_args = {
    # ADR-019: Enable Delta Lake format support via --datalake-formats.
    "--datalake-formats" = "delta"

    # Delta Spark session extensions — must be passed as a single --conf string.
    "--conf" = join(" ", [
      "spark.sql.extensions=io.delta.sql.DeltaSparkSessionExtension",
      "--conf spark.sql.catalog.spark_catalog=org.apache.spark.sql.delta.catalog.DeltaCatalog",
    ])

    # Continuous CloudWatch logging for real-time Glue job monitoring.
    "--enable-continuous-cloudwatch-log" = "true"
    "--enable-metrics"                   = "true"
    "--enable-spark-ui"                  = "true"

    # Temp directory for Glue shuffle and spill.
    "--TempDir" = "s3://${var.artifacts_bucket_name}/tmp/"

    # Shared lakehouse library wheel — contains schemas, transforms, merge logic.
    "--extra-py-files" = "s3://${var.artifacts_bucket_name}/wheels/lakehouse-latest.whl"
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# GLUE DATA CATALOG DATABASE
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_glue_catalog_database" "main" {
  name        = local.db_name
  description = "E-commerce Lakehouse Delta tables — ${var.env} environment."

  # Location URI is optional for Delta — Glue uses the StorageDescriptor per-table.
}

# ─────────────────────────────────────────────────────────────────────────────
# GLUE CATALOG TABLES
# Registered as Delta type so Athena v3 reads the transaction log natively.
# Schemas match the Design Contract §3.3 exactly (Glue/Athena column types).
# Audit columns (_ingest_ts, _source_file, _batch_id, _record_hash) appended to all.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_glue_catalog_table" "dim_products" {
  name          = "dim_products"
  database_name = aws_glue_catalog_database.main.name

  table_type = "EXTERNAL_TABLE"

  parameters = {
    # Tell Glue/Athena this is a Delta table — enables native transaction log reads.
    "classification"                    = "delta"
    "table_type"                        = "DELTA"
    "delta.compatible.checksum.enabled" = "true"
  }

  storage_descriptor {
    location      = "s3://${var.dwh_bucket_name}/dim_products/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    }

    # Business columns (Design Contract §3.3 — dim_products).
    columns {
      name    = "product_id"
      type    = "int"
      comment = "PK — unique product identifier"
    }

    columns {
      name    = "department_id"
      type    = "int"
      comment = "FK to department lookup"
    }

    columns {
      name    = "department"
      type    = "string"
      comment = "One of: Books, Sports, Toys, Home, Clothing, Electronics"
    }

    columns {
      name = "product_name"
      type = "string"
    }

    # Audit columns (Design Contract §3.3 — appended to every Delta table).
    columns {
      name    = "_ingest_ts"
      type    = "timestamp"
      comment = "UTC timestamp when this record was ingested"
    }

    columns {
      name    = "_source_file"
      type    = "string"
      comment = "S3 key of the source file"
    }

    columns {
      name    = "_batch_id"
      type    = "string"
      comment = "batch_id = {dataset}-{yyyymmdd}-{short_uuid}"
    }

    columns {
      name    = "_record_hash"
      type    = "string"
      comment = "SHA-256 of business columns for dedup"
    }
  }
}

resource "aws_glue_catalog_table" "fct_orders" {
  name          = "fct_orders"
  database_name = aws_glue_catalog_database.main.name

  table_type = "EXTERNAL_TABLE"

  parameters = {
    "classification"                    = "delta"
    "table_type"                        = "DELTA"
    "delta.compatible.checksum.enabled" = "true"
  }

  storage_descriptor {
    location      = "s3://${var.dwh_bucket_name}/fct_orders/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    }

    # Business columns (Design Contract §3.3 — fct_orders).
    columns {
      name = "order_num"
      type = "int"
    }

    columns {
      name    = "order_id"
      type    = "bigint"
      comment = "PK / merge key — unique order identifier"
    }

    columns {
      name = "user_id"
      type = "bigint"
    }

    columns {
      name = "order_timestamp"
      type = "timestamp"
    }

    columns {
      name = "total_amount"
      type = "decimal(10,2)"
    }

    columns {
      name    = "order_date"
      type    = "date"
      comment = "Z-Order key (ADR-005) — partition-ready at ≥1 GB/partition"
    }

    # Audit columns.
    columns {
      name = "_ingest_ts"
      type = "timestamp"
    }

    columns {
      name = "_source_file"
      type = "string"
    }

    columns {
      name = "_batch_id"
      type = "string"
    }

    columns {
      name = "_record_hash"
      type = "string"
    }
  }
}

resource "aws_glue_catalog_table" "fct_order_items" {
  name          = "fct_order_items"
  database_name = aws_glue_catalog_database.main.name

  table_type = "EXTERNAL_TABLE"

  parameters = {
    "classification"                    = "delta"
    "table_type"                        = "DELTA"
    "delta.compatible.checksum.enabled" = "true"
  }

  storage_descriptor {
    location      = "s3://${var.dwh_bucket_name}/fct_order_items/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    }

    # Business columns (Design Contract §3.3 — fct_order_items).
    columns {
      name    = "id"
      type    = "bigint"
      comment = "PK / merge key"
    }

    columns {
      name    = "order_id"
      type    = "bigint"
      comment = "FK → fct_orders.order_id"
    }

    columns {
      name = "user_id"
      type = "bigint"
    }

    columns {
      name    = "days_since_prior_order"
      type    = "int"
      comment = "0–365 (observed 0–30)"
    }

    columns {
      name    = "product_id"
      type    = "int"
      comment = "FK → dim_products.product_id"
    }

    columns {
      name    = "add_to_cart_order"
      type    = "int"
      comment = "≥ 1"
    }

    columns {
      name    = "reordered"
      type    = "int"
      comment = "∈ {0,1}"
    }

    columns {
      name = "order_timestamp"
      type = "timestamp"
    }

    columns {
      name    = "order_date"
      type    = "date"
      comment = "Z-Order key (ADR-005)"
    }

    # Audit columns.
    columns {
      name = "_ingest_ts"
      type = "timestamp"
    }

    columns {
      name = "_source_file"
      type = "string"
    }

    columns {
      name = "_batch_id"
      type = "string"
    }

    columns {
      name = "_record_hash"
      type = "string"
    }
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# ATHENA WORKGROUP
# ADR-015: Athena v3 reads Delta natively; enforce output to athena-results bucket.
# Note: workgroup enforces query result location — users cannot override it.
# ─────────────────────────────────────────────────────────────────────────────

# Athena workgroup is not in scope of this module (it needs the athena-results bucket
# ARN which is in s3_zones). Declare it in the dev/main.tf or a separate module.
# Placeholder comment preserved for traceability.

# ─────────────────────────────────────────────────────────────────────────────
# GLUE JOB: INGEST
# Per-dataset invocation pattern (ADR-011): Step Functions passes --dataset argument.
# ADR-020: G.1X, NumberOfWorkers=2, auto-scaling disabled (no max_capacity).
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_glue_job" "ingest" {
  name         = "${var.prefix}-ingest-${var.env}"
  role_arn     = var.glue_role_arn
  glue_version = "4.0"

  # ADR-020: fixed 2 workers, G.1X, auto-scaling disabled.
  worker_type       = "G.1X"
  number_of_workers = 2

  # Step Functions owns retries — set max_retries=0 here to avoid double-retry.
  max_retries = 0

  # 30-minute timeout per job run; Step Functions enforces overall pipeline timeout.
  timeout = 30

  command {
    name            = "glueetl"
    script_location = "s3://${var.artifacts_bucket_name}/scripts/ingest.py"
    python_version  = "3"
  }

  default_arguments = merge(local.common_glue_args, {
    # Dataset name injected by Step Functions at StartJobRun time:
    # e.g. --dataset=orders, --dataset=products, --dataset=order_items.
    "--dataset"    = ""
    "--env"        = var.env
    "--dwh-bucket" = var.dwh_bucket_name
  })

  # Execution property: only one concurrent run per job name (Step Functions serialises).
  execution_property {
    max_concurrent_runs = 3
  }

  tags = {
    Name    = "${var.prefix}-ingest-${var.env}"
    Purpose = "glue-spark-etl-ingest"
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# GLUE JOB: OPTIMIZE
# Runs OPTIMIZE + VACUUM on Delta tables post-ingest (Z-ORDER maintenance).
# ADR-005: Z-ORDER by order_date; no physical partitioning at current volume.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_glue_job" "optimize" {
  name         = "${var.prefix}-optimize-${var.env}"
  role_arn     = var.glue_role_arn
  glue_version = "4.0"

  worker_type       = "G.1X"
  number_of_workers = 2
  max_retries       = 0
  timeout           = 30

  command {
    name            = "glueetl"
    script_location = "s3://${var.artifacts_bucket_name}/scripts/optimize.py"
    python_version  = "3"
  }

  default_arguments = merge(local.common_glue_args, {
    "--env"        = var.env
    "--dwh-bucket" = var.dwh_bucket_name
    # Tables to optimize (comma-separated); Step Functions can override per run.
    "--tables" = "dim_products,fct_orders,fct_order_items"
  })

  execution_property {
    max_concurrent_runs = 1
  }

  tags = {
    Name    = "${var.prefix}-optimize-${var.env}"
    Purpose = "glue-spark-delta-optimize"
  }
}
