"""
optimize.py — Glue entrypoint for OPTIMIZE + VACUUM on Delta tables.

This job is intentionally separate from ingest.py because:
  1. OPTIMIZE is an expensive compaction operation that should not block
     the critical path of each individual dataset load.
  2. Step Functions can schedule this job once, after ALL dataset MERGE jobs
     in a pipeline run have completed successfully, to compact all tables in
     one Glue session rather than one session per dataset.
  3. Decoupling allows running OPTIMIZE on a schedule (e.g. nightly) without
     triggering a full re-ingest.

OPTIMIZE must be run post-load to:
  - Compact the small Parquet files produced by the Delta MERGE into fewer,
    larger files — reduces Athena partition scan cost and query latency.
  - Re-cluster data by Z-ORDER columns for improved data skipping.

VACUUM is run after OPTIMIZE to remove obsolete files outside the retention window.
"""

import argparse
import sys

from lakehouse import config, io as lake_io
from lakehouse import merge as lake_merge
from lakehouse import logging_utils
from lakehouse.config import DATASET_LOAD_ORDER, DATASET_TO_TABLE, ZORDER_COLS


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="ecom-lakehouse OPTIMIZE + VACUUM Glue job"
    )
    parser.add_argument(
        "--dataset",
        default="all",
        help=(
            'Short dataset name to optimize, or "all" to optimize every table '
            "(default: all)"
        ),
    )
    parser.add_argument(
        "--env",
        default="dev",
        choices=["dev", "prod"],
        help="Deployment environment (default: dev)",
    )
    parser.add_argument(
        "--dwh_bucket",
        default=None,
        help="DWH S3 bucket name (default: ecom-lakehouse-dwh-{env})",
    )
    parser.add_argument(
        "--vacuum_retain_hours",
        type=int,
        default=168,
        help="VACUUM retention window in hours (default: 168 = 7 days)",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    logger = logging_utils.get_logger(__name__)

    env = args.env
    dwh_bucket = args.dwh_bucket or f"{config.PROJECT_PREFIX}-dwh-{env}"

    # Determine which datasets to process
    if args.dataset == "all":
        datasets = DATASET_LOAD_ORDER
    else:
        if args.dataset not in DATASET_TO_TABLE:
            logger.error("Unknown dataset: %s", args.dataset)
            sys.exit(1)
        datasets = [args.dataset]

    logger.info(
        "Optimize job started",
        extra={"datasets": datasets, "env": env, "vacuum_retain_hours": args.vacuum_retain_hours},
    )

    # Create SparkSession with Delta extensions
    spark = lake_io.delta_session(
        app_name=f"ecom-lakehouse-optimize-{args.dataset}-{env}",
        enable_hive_catalog=False,
    )

    for dataset in datasets:
        table_name = DATASET_TO_TABLE[dataset]
        table_path = f"s3://{dwh_bucket}/{table_name}/"
        zorder_cols = ZORDER_COLS[dataset]

        logger.info(
            "Processing dataset=%s table=%s path=%s",
            dataset,
            table_name,
            table_path,
        )

        # Guard: skip if the Delta table does not yet exist (e.g. first pipeline
        # run was interrupted before this dataset was loaded).
        if not lake_io.delta_table_exists(spark, table_path):
            logger.warning(
                "Delta table not found at %s — skipping OPTIMIZE/VACUUM for %s",
                table_path,
                dataset,
            )
            continue

        # Run OPTIMIZE ZORDER BY
        # This compacts small files and clusters data by the most-filtered columns.
        logger.info(
            "Running OPTIMIZE ZORDER BY (%s) on %s",
            ", ".join(zorder_cols),
            table_path,
        )
        lake_merge.run_optimize(spark, table_path, zorder_cols)

        # Run VACUUM
        # Removes obsolete Parquet files outside the retention window.
        # Always run after OPTIMIZE so the compacted files are retained but
        # the pre-compaction small files are eventually cleaned up.
        logger.info(
            "Running VACUUM (retain=%d hours) on %s",
            args.vacuum_retain_hours,
            table_path,
        )
        lake_merge.run_vacuum(spark, table_path, retain_hours=args.vacuum_retain_hours)

        logger.info("OPTIMIZE + VACUUM complete for dataset=%s", dataset)

    logger.info("Optimize job finished for all requested datasets")


if __name__ == "__main__":
    main(sys.argv[1:])
