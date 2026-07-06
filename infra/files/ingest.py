"""
ingest.py — Thin Glue entrypoint for the ecom-lakehouse ingestion pipeline.

This script is the per-dataset Glue job entry point.  It is deliberately thin:
all business logic lives in the src/lakehouse/ library modules.  Keeping the
entrypoint thin means the library is independently testable without a Glue runtime.

Pipeline steps:
  1.  delta_session()          — create SparkSession with Delta extensions
  2.  read_staging()           — read normalized Parquet from staging zone
  3.  enforce_types()          — cast columns to canonical types; flag cast failures
  4.  derive()                 — add order_date, audit cols (_ingest_ts etc.), _record_hash
  5.  apply_rules()            — run business-rule validation; split valid/rejected
  6.  referential_integrity()  — FK checks (order_items only); split valid/orphans
  7.  dedup()                  — within-batch dedup; keep 1 row per merge key
  8.  write_quarantine()       — write rejected + orphan rows to quarantine zone
  9.  compute_metrics()        — count rows_in/valid/rejected/dedup_collapsed
  10. gate()                   — abort if reject_rate > threshold
  11. upsert()                 — Delta MERGE (create on first run)
  12. ledger.mark_loaded()     — update DynamoDB ledger status to LOADED
  13. ledger.update_watermark() — update watermarks table with latest period

Glue configuration (ADR-020): G.1X workers, count=2 (API minimum), auto-scaling OFF.
DynamicFrames PROHIBITED (ADR-019) — native Spark DataFrames throughout.
"""

import argparse
import sys
import traceback

from lakehouse import config
from lakehouse import io as lake_io
from lakehouse import logging_utils
from lakehouse import merge as lake_merge
from lakehouse import transforms
from lakehouse import validation as val
from lakehouse.config import DATASET_TO_TABLE, MERGE_KEY
from lakehouse.ledger import LedgerClient
from lakehouse.schemas import SCHEMAS


def parse_args(argv=None):
    """Parse Glue job arguments from the command line (or GlueContext.getResolvedOptions)."""
    parser = argparse.ArgumentParser(
        description="ecom-lakehouse ingest Glue job: validate + MERGE into Delta"
    )
    parser.add_argument(
        "--dataset",
        required=True,
        choices=["products", "orders", "order_items"],
        help="Short dataset name to process",
    )
    parser.add_argument(
        "--batch_id",
        required=True,
        help="Batch identifier (format: {dataset}-{yyyymmdd}-{short_uuid})",
    )
    parser.add_argument(
        "--staging_uri",
        default=None,
        help="Full S3 URI of the staging Parquet prefix for this batch "
             "(default: derived from --dataset and --batch_id)",
    )
    parser.add_argument(
        "--file_key",
        default=None,
        help="Raw S3 key passed by Step Functions (used to derive --source_file)",
    )
    parser.add_argument(
        "--source_file",
        default=None,
        help="S3 URI of the original raw file (for _source_file audit column; "
             "default: derived from --file_key)",
    )
    parser.add_argument(
        "--env",
        default="dev",
        choices=["dev", "prod"],
        help="Deployment environment (default: dev)",
    )
    parser.add_argument(
        "--reject_threshold",
        type=float,
        default=config.REJECT_THRESHOLD,
        help=f"Max allowed reject_rate (default: {config.REJECT_THRESHOLD})",
    )
    parser.add_argument(
        "--dwh_bucket",
        default=None,
        help="DWH S3 bucket name (default: ecom-lakehouse-dwh-{env})",
    )
    parser.add_argument(
        "--quarantine_bucket",
        default=None,
        help="Quarantine S3 bucket name (default: ecom-lakehouse-quarantine-{env})",
    )
    # parse_known_args ignores Glue-injected args (--JOB_ID, --JOB_RUN_ID, etc.)
    args, _ = parser.parse_known_args(argv)
    return args


def main(argv=None):
    """
    Main entry point — runs the full ingest pipeline for one dataset batch.

    Catches all exceptions, marks the ledger as FAILED, emits a CloudWatch
    metric, then re-raises so that Glue/Step Functions marks the job as failed.
    """
    args = parse_args(argv)

    # Derive optional args from mandatory ones when not provided.
    # This keeps the Step Functions ASL minimal — it only passes --dataset,
    # --file_key (or --batch_id), and --batch_id.
    env = args.env
    staging_bucket = f"{config.PROJECT_PREFIX}-staging-{env}"
    raw_bucket = f"{config.PROJECT_PREFIX}-raw-{env}"

    if args.staging_uri is None:
        args.staging_uri = (
            f"s3://{staging_bucket}/{args.dataset}/batch_id={args.batch_id}/part.parquet"
        )
    if args.source_file is None:
        # file_key is the raw S3 key (no bucket prefix); use it directly as source_file
        raw_key = args.file_key or ""
        args.source_file = f"s3://{raw_bucket}/{raw_key}" if raw_key else ""

    logger = logging_utils.get_logger(__name__)
    logger.info(
        "Ingest job started",
        extra={
            "dataset": args.dataset,
            "batch_id": args.batch_id,
            "env": env,
            "staging_uri": args.staging_uri,
        },
    )

    # Resolve bucket names (use arg override or default from config)
    dwh_bucket = args.dwh_bucket or f"{config.PROJECT_PREFIX}-dwh-{env}"
    quarantine_bucket = args.quarantine_bucket or f"{config.PROJECT_PREFIX}-quarantine-{env}"

    # Derive the table path and merge key from the dataset name
    table_name = DATASET_TO_TABLE[args.dataset]
    target_path = f"s3://{dwh_bucket}/{table_name}/"
    merge_key = MERGE_KEY[args.dataset]
    schema = SCHEMAS[args.dataset]

    # file_key is used as the DynamoDB ledger PK (raw S3 key without bucket prefix)
    file_key = args.source_file.replace(f"s3://{raw_bucket}/", "") if args.source_file else (args.file_key or "")

    ledger = LedgerClient(env=env)

    try:
        # ------------------------------------------------------------------
        # Step 1: Create SparkSession with Delta Lake extensions
        # ------------------------------------------------------------------
        logger.info("Step 1: Creating SparkSession")
        spark = lake_io.delta_session(
            app_name=f"ecom-lakehouse-ingest-{args.dataset}-{args.batch_id}",
            enable_hive_catalog=False,
        )

        # ------------------------------------------------------------------
        # Step 2: Read normalized Parquet from staging
        # ------------------------------------------------------------------
        logger.info("Step 2: Reading staging Parquet from %s", args.staging_uri)
        df_raw = lake_io.read_staging(spark, args.staging_uri, schema)
        rows_in_count = df_raw.cache().count()
        logger.info("Step 2 complete: rows_in=%d", rows_in_count)

        # ------------------------------------------------------------------
        # Step 3: Enforce canonical column types; flag cast failures
        # ------------------------------------------------------------------
        logger.info("Step 3: Enforcing types for dataset=%s", args.dataset)
        df_typed = transforms.enforce_types(df_raw, args.dataset)

        # Separate rows with cast failures from clean rows
        import pyspark.sql.functions as F

        df_cast_failed = df_typed.filter(F.col("_cast_failed")).drop("_cast_failed")
        df_cast_ok = df_typed.filter(~F.col("_cast_failed")).drop("_cast_failed")
        logger.info(
            "Step 3 complete: cast_failed=%d, cast_ok=%d",
            df_cast_failed.count(),
            df_cast_ok.count(),
        )

        # ------------------------------------------------------------------
        # Step 4: Derive order_date, audit columns, and _record_hash
        # ------------------------------------------------------------------
        logger.info("Step 4: Deriving audit columns and record hash")
        df_derived = transforms.derive(df_cast_ok, args.dataset, args.batch_id, args.source_file)

        # ------------------------------------------------------------------
        # Step 5: Apply business-rule validation
        # ------------------------------------------------------------------
        logger.info("Step 5: Applying business rules for dataset=%s", args.dataset)
        rules = val.RULES[args.dataset]
        df_valid, df_rejected_rules = val.apply_rules(df_derived, rules)

        # Also treat cast-failed rows as rejected (with a reason column)
        if df_cast_failed.count() > 0:
            df_cast_failed_with_reason = df_cast_failed.withColumn(
                "reject_reason", F.lit("TYPE_CAST: one or more required columns could not be cast")
            )
            # Union with rule-rejected rows
            df_rejected_all = df_rejected_rules.unionByName(
                df_cast_failed_with_reason, allowMissingColumns=True
            )
        else:
            df_rejected_all = df_rejected_rules

        logger.info(
            "Step 5 complete: valid=%d, rejected=%d",
            df_valid.count(),
            df_rejected_all.count(),
        )

        # ------------------------------------------------------------------
        # Step 6: Referential integrity checks (order_items only)
        # ------------------------------------------------------------------
        logger.info("Step 6: Referential integrity check")
        df_ri_valid, df_orphans = val.referential_integrity(
            spark, df_valid, args.dataset, dwh_bucket, env
        )

        # Combine orphans into the overall rejected set
        if df_orphans.count() > 0:
            df_rejected_all = df_rejected_all.unionByName(df_orphans, allowMissingColumns=True)

        logger.info(
            "Step 6 complete: ri_valid=%d, orphans=%d",
            df_ri_valid.count(),
            df_orphans.count(),
        )

        # ------------------------------------------------------------------
        # Step 7: Within-batch deduplication
        # ------------------------------------------------------------------
        logger.info("Step 7: Deduplicating on merge_key=%s", merge_key)
        df_before_dedup = df_ri_valid  # reference for dedup_collapsed count
        df_deduped = transforms.dedup(df_ri_valid, merge_key)
        logger.info("Step 7 complete: rows after dedup=%d", df_deduped.count())

        # ------------------------------------------------------------------
        # Step 8: Write rejected rows to quarantine zone
        # ------------------------------------------------------------------
        logger.info("Step 8: Writing %d rejected rows to quarantine", df_rejected_all.count())
        if df_rejected_all.count() > 0:
            quarantine_uri = lake_io.write_quarantine(
                df_rejected_all,
                args.dataset,
                args.batch_id,
                quarantine_bucket,
                env,
            )
            logger.info("Step 8 complete: quarantine_uri=%s", quarantine_uri)
        else:
            logger.info("Step 8: no rejected rows — quarantine write skipped")

        # ------------------------------------------------------------------
        # Step 9: Compute pipeline quality metrics
        # ------------------------------------------------------------------
        logger.info("Step 9: Computing metrics")
        metrics = logging_utils.compute_metrics(
            df_in=df_raw,
            df_valid=df_deduped,
            df_rejected=df_rejected_all,
            df_dedup_collapsed=df_before_dedup,
        )
        logger.info("Step 9 metrics: %s", metrics)

        # Emit to CloudWatch
        logging_utils.emit_cloudwatch_metric(
            namespace=f"{config.PROJECT_PREFIX}/ingestion",
            metric_name="rows_rejected",
            value=float(metrics["rows_rejected"]),
            dimensions=[
                {"Name": "dataset", "Value": args.dataset},
                {"Name": "env", "Value": env},
            ],
        )
        logging_utils.emit_cloudwatch_metric(
            namespace=f"{config.PROJECT_PREFIX}/ingestion",
            metric_name="reject_rate",
            value=float(metrics["reject_rate"]),
            dimensions=[
                {"Name": "dataset", "Value": args.dataset},
                {"Name": "env", "Value": env},
            ],
            unit="None",
        )

        # ------------------------------------------------------------------
        # Step 10: Quality gate — abort if reject_rate > threshold
        # ------------------------------------------------------------------
        logger.info(
            "Step 10: Quality gate (threshold=%.4f, actual=%.4f)",
            args.reject_threshold,
            metrics["reject_rate"],
        )
        logging_utils.gate(metrics, args.reject_threshold)
        logger.info("Step 10: Quality gate PASSED")

        # ------------------------------------------------------------------
        # Step 11: MERGE into Delta table (create on first run)
        # ------------------------------------------------------------------
        logger.info("Step 11: Upserting into Delta table at %s", target_path)
        merge_result = lake_merge.upsert(spark, df_deduped, target_path, merge_key)
        logger.info("Step 11 complete: action=%s", merge_result["action"])

        # ------------------------------------------------------------------
        # Step 12: Update DynamoDB ledger → LOADED
        # ------------------------------------------------------------------
        logger.info("Step 12: Marking ledger LOADED for file_key=%s", file_key)
        ledger.mark_loaded(file_key, metrics)

        # ------------------------------------------------------------------
        # Step 13: Update watermarks table
        # ------------------------------------------------------------------
        # Derive period from batch_id: format is "{dataset}-{yyyymmdd}-{uuid}"
        # Extract the yyyymmdd part and convert to YYYYMM period.
        logger.info("Step 13: Updating watermark for dataset=%s", args.dataset)
        try:
            period = args.batch_id.split("-")[1][:6]  # e.g. "20250401" → "202504"
        except (IndexError, ValueError):
            period = "000000"  # fallback; should not happen with correct batch_id format
        ledger.update_watermark(args.dataset, period, args.batch_id)

        logger.info(
            "Ingest job completed successfully",
            extra={"dataset": args.dataset, "batch_id": args.batch_id, **metrics},
        )

    except Exception as exc:
        logger.error(
            "Ingest job FAILED: %s\n%s",
            exc,
            traceback.format_exc(),
        )
        # Mark the ledger entry as FAILED so the pipeline can be retried
        try:
            ledger.mark_failed(file_key, str(exc))
        except Exception as ledger_exc:
            logger.error("Could not update ledger to FAILED: %s", ledger_exc)

        # Re-raise so Glue reports the job as failed and Step Functions can retry
        raise


if __name__ == "__main__":
    main(sys.argv[1:])
