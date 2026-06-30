"""
scripts/demo_spinup.py — One-command demo spin-up for the ecom-lakehouse pipeline.

Orchestration order (Senior DE best-practice: idempotent, observable, fail-fast):
  1.  terraform apply        — provision / reconcile all AWS infrastructure
  2.  deploy_lambdas         — push real Lambda code (overcomes placeholder.zip bootstrap)
  3.  build & upload wheel   — package the lakehouse PySpark library and push to S3 artifacts
  4.  upload Glue scripts    — push ingest.py / optimize.py to S3 artifacts
  5.  clean_slate            — reset S3 data zones + DynamoDB ledger (idempotent)
  6.  upload sample data     — trigger S3 event → EventBridge → Step Functions
  7.  poll Step Functions    — wait for all 3 executions to reach SUCCEEDED / FAILED
  8.  report                 — print a summary with pass/fail for each dataset

Usage (PowerShell):
    $env:AWS_PROFILE = "sandbox-lakehouse-dev"
    python scripts/demo_spinup.py

Exit codes:
    0  — all pipelines succeeded
    1  — one or more pipelines failed (see report)
    2  — infrastructure or deployment step failed
"""

import os
import subprocess
import sys
import time
import zipfile
import shutil
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

STATE_MACHINE_ARN = (
    f"arn:aws:states:{REGION}:970547336735:"
    f"stateMachine:{PREFIX}-sm-{ENV}"
)
ARTIFACTS_BUCKET = f"{PREFIX}-artifacts-{ENV}"
RAW_BUCKET       = f"{PREFIX}-raw-{ENV}"

DATA_FILES = [
    ("Data/products.csv",               "dim_products/2025/04/products.csv"),
    ("Data/orders_apr_2025.xlsx",       "fct_orders/2025/04/orders_apr_2025.xlsx"),
    ("Data/order_items_apr_2025.xlsx",  "fct_order_items/2025/04/order_items_apr_2025.xlsx"),
]

LAMBDAS = [
    {"name": f"{PREFIX}-claim-file-{ENV}",       "entry": "src/lambdas/claim_file.py",           "zip_name": "claim_file.py"},
    {"name": f"{PREFIX}-archive-file-{ENV}",     "entry": "src/lambdas/archive_file.py",          "zip_name": "archive_file.py"},
    {"name": f"{PREFIX}-validate-schema-{ENV}",  "entry": "src/lambdas/validate_schema.py",       "zip_name": "validate_schema.py"},
    {"name": f"{PREFIX}-normalize-{ENV}",        "entry": "src/normalize/normalize_to_parquet.py","zip_name": "normalize.py"},
]

POLL_INTERVAL_S  = 20   # seconds between status checks
POLL_TIMEOUT_S   = 900  # 15 min max wait for pipelines

errors = []  # accumulated non-fatal errors for final report

# ─── Helpers ──────────────────────────────────────────────────────────────────

def banner(title: str):
    width = 60
    print("\n" + "=" * width)
    print(f"  {title}")
    print("=" * width)


def run(cmd: str, cwd: str = ROOT_DIR, check: bool = True) -> int:
    """Run a shell command, streaming output.  Returns exit code."""
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


def zip_dir(path: str, ziph, prefix: str = ""):
    """Recursively add Python files from a directory into a zip."""
    for root, _dirs, files in os.walk(path):
        for file in files:
            if file.endswith(".py"):
                file_path = os.path.join(root, file)
                rel_path  = os.path.relpath(file_path, path)
                ziph.write(file_path, os.path.join(prefix, rel_path))


# ─── Step 1: Terraform apply ──────────────────────────────────────────────────

def step_terraform():
    banner("STEP 1 / 7 — Terraform Apply")
    run(
        f'terraform apply "-var-file={TFVARS}" "-auto-approve"',
        cwd=INFRA_DIR
    )
    print("\n[✓] Infrastructure is up-to-date.")


# ─── Step 2: Deploy Lambdas ───────────────────────────────────────────────────

def step_deploy_lambdas():
    banner("STEP 2 / 7 — Deploy Lambda Functions")
    session = get_session()
    client  = session.client("lambda")
    os.makedirs(os.path.join(ROOT_DIR, "dist"), exist_ok=True)

    for info in LAMBDAS:
        zip_path = os.path.join(ROOT_DIR, "dist", f"{info['name']}.zip")
        print(f"\n[*] Packaging {info['name']}…")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            zipf.write(os.path.join(ROOT_DIR, info["entry"]), info["zip_name"])
            zip_dir(os.path.join(ROOT_DIR, "src", "lakehouse"), zipf, prefix="lakehouse")

        with open(zip_path, "rb") as f:
            code = f.read()

        try:
            resp = client.update_function_code(FunctionName=info["name"], ZipFile=code)
            print(f"    [✓] Deployed → version {resp.get('Version', '$LATEST')}")
        except Exception as e:
            msg = f"Lambda deploy failed for {info['name']}: {e}"
            print(f"    [✗] {msg}")
            errors.append(msg)

    print("\n[✓] Lambda deployment complete.")


# ─── Step 3 & 4: Build wheel + upload Glue artifacts ─────────────────────────

def step_glue_artifacts():
    banner("STEP 3 & 4 / 7 — Build PySpark Wheel + Upload Glue Artifacts")
    session = get_session()
    s3      = session.client("s3")

    # Build wheel
    dist_dir = os.path.join(ROOT_DIR, "dist")
    os.makedirs(dist_dir, exist_ok=True)
    run(f"pip wheel --no-deps -w {dist_dir} {ROOT_DIR}")

    # Find the built wheel (name may vary)
    wheels = list(Path(dist_dir).glob("*.whl"))
    if not wheels:
        raise RuntimeError("No wheel found in dist/ after build. Check pyproject.toml.")
    wheel_path = str(sorted(wheels)[-1])
    print(f"\n[*] Built wheel: {wheel_path}")

    # Upload wheel
    s3.upload_file(wheel_path, ARTIFACTS_BUCKET, "wheels/lakehouse-latest.whl")
    print(f"[✓] Uploaded wheel → s3://{ARTIFACTS_BUCKET}/wheels/lakehouse-latest.whl")

    # Upload Glue ETL scripts
    for script_name in ("ingest.py", "optimize.py"):
        local = os.path.join(ROOT_DIR, "src", "glue_jobs", script_name)
        key   = f"scripts/{script_name}"
        s3.upload_file(local, ARTIFACTS_BUCKET, key)
        print(f"[✓] Uploaded script → s3://{ARTIFACTS_BUCKET}/{key}")

    print("\n[✓] Glue artifacts ready.")


# ─── Step 5: Clean slate ──────────────────────────────────────────────────────

def step_clean_slate():
    banner("STEP 5 / 7 — Clean Slate (Reset data zones)")
    run(f"python scripts/clean_slate.py")
    print("\n[✓] Environment is clean.")


# ─── Step 6: Upload sample data ───────────────────────────────────────────────

def step_upload_data():
    banner("STEP 6 / 7 — Upload Sample Data → Trigger Pipeline")
    session = get_session()
    s3      = session.client("s3")

    for local_path, s3_key in DATA_FILES:
        full_local = os.path.join(ROOT_DIR, local_path)
        if not os.path.exists(full_local):
            msg = f"Sample data file not found: {full_local}"
            print(f"[✗] {msg}")
            errors.append(msg)
            continue
        s3.upload_file(full_local, RAW_BUCKET, s3_key)
        print(f"[✓] Uploaded {local_path} → s3://{RAW_BUCKET}/{s3_key}")

    print("\n[✓] All sample files uploaded. Step Functions executions starting…")


# ─── Step 7: Poll Step Functions ──────────────────────────────────────────────

def step_poll_pipelines():
    banner("STEP 7 / 7 — Polling Step Functions Executions")
    session  = get_session()
    sfn      = session.client("stepfunctions")

    # Allow a few seconds for EventBridge to fire
    print("[*] Waiting 15 s for EventBridge to trigger executions…")
    time.sleep(15)

    start_ts = datetime.now(timezone.utc)
    deadline = time.time() + POLL_TIMEOUT_S
    report   = {}   # execution_arn → final status

    print(f"[*] Polling every {POLL_INTERVAL_S}s (timeout {POLL_TIMEOUT_S}s)…\n")

    while time.time() < deadline:
        paginator = sfn.get_paginator("list_executions")
        pages = paginator.paginate(stateMachineArn=STATE_MACHINE_ARN)

        running = []
        for page in pages:
            for ex in page["executions"]:
                # Only consider executions started after this script began
                if ex["startDate"] < start_ts:
                    continue
                arn    = ex["executionArn"]
                status = ex["status"]
                report[arn] = {"status": status, "name": ex["name"]}
                if status == "RUNNING":
                    running.append(arn)

        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(f"[{ts}] Tracked executions: {len(report)} | Still running: {len(running)}")
        for arn, info in report.items():
            short = arn.split(":")[-1][:40]
            print(f"        {info['status']:<12}  {short}")

        if report and not running:
            break   # All executions have reached a terminal state

        time.sleep(POLL_INTERVAL_S)
    else:
        errors.append(f"Pipeline poll timed out after {POLL_TIMEOUT_S}s. Some executions may still be running.")
        print(f"\n[!] Timeout reached after {POLL_TIMEOUT_S}s.")

    return report


# ─── Final report ─────────────────────────────────────────────────────────────

def print_report(sfn_report: dict):
    banner("DEMO SPIN-UP REPORT")
    all_ok = True

    if sfn_report:
        print(f"\n{'EXECUTION':<50}  {'STATUS'}")
        print("-" * 65)
        for arn, info in sfn_report.items():
            status = info["status"]
            icon   = "✓" if status == "SUCCEEDED" else "✗"
            print(f"[{icon}] {arn.split(':')[-1][:46]:<46}  {status}")
            if status != "SUCCEEDED":
                all_ok = False
                errors.append(f"Execution {arn} ended with status: {status}")
    else:
        print("\n[!] No Step Functions executions were tracked (did EventBridge fire?).")
        all_ok = False
        errors.append("No Step Functions executions tracked. Check EventBridge rule.")

    if errors:
        print(f"\n{'─'*60}")
        print("ISSUES ENCOUNTERED:")
        for i, e in enumerate(errors, 1):
            print(f"  {i}. {e}")

    print(f"\n{'─'*60}")
    if all_ok:
        print("✅  DEMO ENVIRONMENT IS READY.  All pipelines succeeded.")
        print(f"    DWH bucket: s3://{PREFIX}-dwh-{ENV}/")
        print(f"    Query via Athena → database: ecom_lakehouse_{ENV}")
    else:
        print("❌  SPIN-UP COMPLETED WITH ERRORS. Review the issues above.")
    print("=" * 60)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 60)
    print("  E-COMMERCE LAKEHOUSE — DEMO SPIN-UP")
    print(f"  Profile: {PROFILE}  |  Region: {REGION}  |  Env: {ENV}")
    print("=" * 60)

    try:
        step_terraform()
    except Exception as e:
        print(f"\n[✗] FATAL: Terraform failed: {e}")
        sys.exit(2)

    try:
        step_deploy_lambdas()
    except Exception as e:
        print(f"\n[✗] FATAL: Lambda deployment failed: {e}")
        sys.exit(2)

    try:
        step_glue_artifacts()
    except Exception as e:
        print(f"\n[✗] FATAL: Glue artifacts upload failed: {e}")
        sys.exit(2)

    step_clean_slate()
    step_upload_data()
    sfn_report = step_poll_pipelines()
    print_report(sfn_report)

    sys.exit(0 if not errors else 1)


if __name__ == "__main__":
    main()
