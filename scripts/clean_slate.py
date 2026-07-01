"""
scripts/clean_slate.py — Clean slate script to empty S3 data buckets and clear DynamoDB tables.

This allows you to quickly reset the data platform to a "day zero" state before a live demo
without having to tear down the infrastructure.

Usage:
    # Run scripts/set_aws_profile.ps1 first, or set AWS_PROFILE=sandbox-lakehouse-dev
    python scripts/clean_slate.py
"""

import os
import boto3

# Self-contained helper to load .env without needing python-dotenv
def load_env():
    if os.path.exists(".env"):
        with open(".env") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    # Set environment variable if not already set
                    if key.strip() not in os.environ:
                        os.environ[key.strip()] = val.strip().strip('"').strip("'")

# Load environment
load_env()

# Resolved bucket names from environment variables
BUCKETS = [
    os.environ.get("S3_RAW_BUCKET", "ecom-lakehouse-raw-dev"),
    os.environ.get("S3_STAGING_BUCKET", "ecom-lakehouse-staging-dev"),
    os.environ.get("S3_DWH_BUCKET", "ecom-lakehouse-dwh-dev"),
    os.environ.get("S3_ARCHIVE_BUCKET", "ecom-lakehouse-archive-dev"),
    os.environ.get("S3_QUARANTINE_BUCKET", "ecom-lakehouse-quarantine-dev"),
    os.environ.get("S3_ATHENA_RESULTS_BUCKET", "ecom-lakehouse-athena-results-dev"),
]

# Resolved DynamoDB table names
TABLES = [
    os.environ.get("DYNAMODB_LEDGER_TABLE", "ecom_lakehouse_ingestion_ledger_dev"),
    os.environ.get("DYNAMODB_WATERMARKS_TABLE", "ecom_lakehouse_watermarks_dev"),
]

region = os.environ.get("AWS_REGION", "eu-west-1")
profile = os.environ.get("AWS_PROFILE", "sandbox-lakehouse-dev")

print("=========================================================")
print(" E-COMMERCE LAKEHOUSE RESET (CLEAN SLATE)")
print("=========================================================")
print(f"Profile: {profile}")
print(f"Region:  {region}\n")

# Initialize Session
try:
    session = boto3.Session(profile_name=profile, region_name=region)
except Exception as e:
    print(f"Could not load profile '{profile}', trying default environment credentials: {e}")
    session = boto3.Session(region_name=region)

s3 = session.resource("s3")
ddb = session.resource("dynamodb")

# 1. Clean S3 Buckets
for bucket_name in BUCKETS:
    print(f"[*] Emptying S3 bucket: {bucket_name}...")
    try:
        bucket = s3.Bucket(bucket_name)
        
        # S3 buckets in this project have versioning enabled.
        # Standard deletions leave delete markers; to fully clean slate we must delete all versions.
        versions = bucket.object_versions.all()
        version_count = 0
        
        # Batch deletion of versions
        version_batch = []
        for v in versions:
            version_batch.append({"Key": v.object_key, "VersionId": v.id})
            if len(version_batch) == 1000:
                bucket.delete_objects(Delete={"Objects": version_batch, "Quiet": True})
                version_count += len(version_batch)
                version_batch = []
                
        if version_batch:
            bucket.delete_objects(Delete={"Objects": version_batch, "Quiet": True})
            version_count += len(version_batch)
            
        print(f"    - Success: Deleted {version_count} object versions.")
    except Exception as e:
        print(f"    - Error: Could not clean bucket {bucket_name}: {e}")

# 2. Clear DynamoDB Tables
for table_name in TABLES:
    print(f"\n[*] Clearing DynamoDB table: {table_name}...")
    try:
        table = ddb.Table(table_name)
        
        # Scan table
        response = table.scan()
        items = response.get("Items", [])
        
        if not items:
            print("    - Table is already empty.")
            continue
            
        # Determine primary keys dynamically
        pk_name = table.key_schema[0]["AttributeName"]
        sk_name = table.key_schema[1]["AttributeName"] if len(table.key_schema) > 1 else None
        
        deleted_count = 0
        with table.batch_writer() as batch:
            for item in items:
                key = {pk_name: item[pk_name]}
                if sk_name:
                    key[sk_name] = item[sk_name]
                batch.delete_item(Key=key)
                deleted_count += 1
                
        print(f"    - Success: Deleted {deleted_count} items.")
    except Exception as e:
        print(f"    - Error: Could not clear DynamoDB table {table_name}: {e}")

print("\n=========================================================")
print(" Clean slate complete. Day-zero demo ready.")
print("=========================================================")
