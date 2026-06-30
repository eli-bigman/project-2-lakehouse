"""
scripts/demo_teardown.py — One-command demo teardown for the ecom-lakehouse pipeline.

Orchestration order (idempotent, safe-to-run any number of times):
  1.  clean_slate    — empty all S3 data zones + clear DynamoDB tables (preserves infra)
  2.  terraform destroy — tear down all AWS resources (uses -auto-approve)
  3.  report         — summary of what was destroyed

Usage (PowerShell):
    $env:AWS_PROFILE = "sandbox-lakehouse-dev"
    python scripts/demo_teardown.py

Notes:
  - The artifacts bucket (Glue wheel + scripts) is also wiped before destroy
    so that versioned objects don't block bucket deletion.
  - S3 versioning is handled by deleting all versions before Terraform destroy.
  - DynamoDB tables are wiped cleanly before Terraform removes them.
  - protect_stateful in dev.tfvars is set to true, which prevents accidental
    deletion of the DWH bucket. Teardown sets it to false for the destroy run.

Exit codes:
    0  — teardown successful
    1  — teardown completed with warnings
    2  — fatal error (infrastructure could not be destroyed)
"""

import os
import sys
import subprocess
import json
from pathlib import Path
from datetime import datetime, timezone

import boto3

# ─── Configuration ────────────────────────────────────────────────────────────
PROFILE    = os.environ.get("AWS_PROFILE", "sandbox-lakehouse-dev")
REGION     = os.environ.get("AWS_REGION",  "eu-west-1")
ENV        = "dev"
PREFIX     = "ecom-lakehouse"
TFVARS     = "dev.tfvars"
INFRA_DIR  = str(Path(__file__).parent.parent / "infra" / "envs" / "dev")
ROOT_DIR   = str(Path(__file__).parent.parent)

# All buckets that need emptying before Terraform destroy
ALL_BUCKETS = [
    f"{PREFIX}-raw-{ENV}",
    f"{PREFIX}-staging-{ENV}",
    f"{PREFIX}-dwh-{ENV}",
    f"{PREFIX}-archive-{ENV}",
    f"{PREFIX}-quarantine-{ENV}",
    f"{PREFIX}-athena-results-{ENV}",
    f"{PREFIX}-artifacts-{ENV}",
]

errors = []


# ─── Helpers ──────────────────────────────────────────────────────────────────

def banner(title: str):
    width = 60
    print("\n" + "=" * width)
    print(f"  {title}")
    print("=" * width)


def run(cmd: str, cwd: str = ROOT_DIR, check: bool = True) -> int:
    print(f"\n[CMD] {cmd}")
    result = subprocess.run(cmd, shell=True, cwd=cwd)
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed (exit {result.returncode}): {cmd}")
    return result.returncode


def get_session():
    try:
        return boto3.Session(profile_name=PROFILE, region_name=REGION)
    except Exception:
        return boto3.Session(region_name=REGION)


# ─── Step 1: Wipe all S3 buckets ─────────────────────────────────────────────

def step_empty_buckets():
    banner("STEP 1 / 2 — Empty ALL S3 Buckets (versions + delete markers)")
    session = get_session()
    s3      = session.resource("s3")

    for bucket_name in ALL_BUCKETS:
        print(f"\n[*] Emptying: {bucket_name}…")
        try:
            bucket  = s3.Bucket(bucket_name)
            batch   = []
            count   = 0
            for v in bucket.object_versions.all():
                batch.append({"Key": v.object_key, "VersionId": v.id})
                if len(batch) == 1000:
                    bucket.delete_objects(Delete={"Objects": batch, "Quiet": True})
                    count += len(batch)
                    batch = []
            if batch:
                bucket.delete_objects(Delete={"Objects": batch, "Quiet": True})
                count += len(batch)
            print(f"    [✓] Deleted {count} object version(s).")
        except s3.meta.client.exceptions.NoSuchBucket:
            print(f"    [~] Bucket does not exist — skipping.")
        except Exception as e:
            msg = f"Could not empty bucket {bucket_name}: {e}"
            print(f"    [✗] {msg}")
            errors.append(msg)


# ─── Step 2: Terraform destroy ────────────────────────────────────────────────

def step_terraform_destroy():
    banner("STEP 2 / 2 — Terraform Destroy")
    # protect_stateful prevents dwh bucket deletion; override it for destroy
    run(
        f'terraform destroy "-var-file={TFVARS}" '
        f'"-var=protect_stateful=false" "-auto-approve"',
        cwd=INFRA_DIR
    )
    print("\n[✓] All infrastructure destroyed.")


# ─── Final report ─────────────────────────────────────────────────────────────

def print_report():
    banner("TEARDOWN REPORT")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"\nCompleted at: {ts}")
    if errors:
        print(f"\n{'─'*60}")
        print("ISSUES ENCOUNTERED:")
        for i, e in enumerate(errors, 1):
            print(f"  {i}. {e}")
        print("\n❌  TEARDOWN COMPLETED WITH WARNINGS. Review the issues above.")
    else:
        print("\n✅  TEARDOWN COMPLETE. All resources removed.")
        print("    Run `python scripts/demo_spinup.py` to spin up again for demo.")
    print("=" * 60)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 60)
    print("  E-COMMERCE LAKEHOUSE — DEMO TEARDOWN")
    print(f"  Profile: {PROFILE}  |  Region: {REGION}  |  Env: {ENV}")
    print("=" * 60)
    print("\n⚠️  This will DESTROY all AWS resources for the dev environment.")

    step_empty_buckets()

    try:
        step_terraform_destroy()
    except Exception as e:
        print(f"\n[✗] FATAL: Terraform destroy failed: {e}")
        errors.append(str(e))
        print_report()
        sys.exit(2)

    print_report()
    sys.exit(0 if not errors else 1)


if __name__ == "__main__":
    main()
