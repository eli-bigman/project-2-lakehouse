"""
scripts/upload_sample_data.py — Upload sample data files to the raw S3 bucket.

Uploads the three provided sample files to the correct S3 key layout:
  s3://<S3_RAW_BUCKET>/<dataset>/<yyyy>/<mm>/<filename>

e.g.:
  s3://ecom-lakehouse-raw-dev/dim_products/2025/04/products.csv
  s3://ecom-lakehouse-raw-dev/fct_orders/2025/04/orders_apr_2025.xlsx
  s3://ecom-lakehouse-raw-dev/fct_order_items/2025/04/order_items_apr_2025.xlsx

Configuration (environment variables or .env file)
---------------------------------------------------
AWS_PROFILE      Named boto3 profile (default: personal).
S3_RAW_BUCKET    Target bucket name.
TF_ENV           Deployment environment suffix, e.g. dev (used to default S3_RAW_BUCKET).
"""

import os
import sys
from pathlib import Path

import boto3

# ---------------------------------------------------------------------------
# Load .env if present (simple key=value, no external library needed)
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DOTENV = _PROJECT_ROOT / ".env"

if _DOTENV.exists():
    with open(_DOTENV) as _fh:
        for _line in _fh:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _val = _line.partition("=")
                os.environ.setdefault(_key.strip(), _val.strip())

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AWS_PROFILE = os.environ.get("AWS_PROFILE", "personal")
TF_ENV = os.environ.get("TF_ENV", "dev")
S3_RAW_BUCKET = os.environ.get("S3_RAW_BUCKET", f"ecom-lakehouse-raw-{TF_ENV}")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

DATA_DIR = _PROJECT_ROOT / "Data"

# Mapping: local filename → (dataset prefix, yyyy, mm)
FILES = [
    ("products.csv", "dim_products", "2025", "04"),
    ("orders_apr_2025.xlsx", "fct_orders", "2025", "04"),
    ("order_items_apr_2025.xlsx", "fct_order_items", "2025", "04"),
]

# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


def get_s3_client() -> boto3.client:
    session = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
    return session.client("s3")


def upload_file(s3_client, local_path: Path, bucket: str, key: str) -> None:
    """Upload a single file and print a confirmation message."""
    print(f"  Uploading {local_path.name} → s3://{bucket}/{key} ...", end=" ")
    s3_client.upload_file(str(local_path), bucket, key)
    print("OK")


def main() -> None:
    print(f"Target bucket : s3://{S3_RAW_BUCKET}")
    print(f"AWS profile   : {AWS_PROFILE}")
    print(f"Data directory: {DATA_DIR}")
    print()

    if not DATA_DIR.exists():
        print(f"ERROR: Data directory not found: {DATA_DIR}", file=sys.stderr)
        sys.exit(1)

    s3 = get_s3_client()

    for filename, dataset, year, month in FILES:
        local_path = DATA_DIR / filename
        if not local_path.exists():
            print(f"  WARNING: {local_path} not found — skipping.", file=sys.stderr)
            continue

        s3_key = f"{dataset}/{year}/{month}/{filename}"
        upload_file(s3, local_path, S3_RAW_BUCKET, s3_key)

    print()
    print("All sample files uploaded successfully.")


if __name__ == "__main__":
    main()
