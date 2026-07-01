"""
scripts/deploy_lambdas.py — Local deployment script to package and upload
the real Python code for the four Lambda functions.

This overcomes the "placeholder.zip" bootstrap limitation by zipping the handler
files along with the shared `lakehouse` package and pushing them directly to AWS.

Usage:
    # Run scripts/set_aws_profile.ps1 first, or set AWS_PROFILE=sandbox-lakehouse-dev
    python scripts/deploy_lambdas.py
"""

import os
import zipfile
import boto3

# Configuration
PROFILE = os.environ.get("AWS_PROFILE", "sandbox-lakehouse-dev")
REGION = os.environ.get("AWS_REGION", "eu-west-1")

LAMBDAS = [
    {
        "name": "ecom-lakehouse-claim-file-dev",
        "entry": "src/lambdas/claim_file.py",
        "zip_name": "claim_file.py"
    },
    {
        "name": "ecom-lakehouse-archive-file-dev",
        "entry": "src/lambdas/archive_file.py",
        "zip_name": "archive_file.py"
    },
    {
        "name": "ecom-lakehouse-validate-schema-dev",
        "entry": "src/lambdas/validate_schema.py",
        "zip_name": "validate_schema.py"
    },
    {
        "name": "ecom-lakehouse-normalize-dev",
        "entry": "src/normalize/normalize_to_parquet.py",
        "zip_name": "normalize.py"
    }
]

def zip_dir(path, ziph, prefix=""):
    """Helper to recursively add a directory to a zip file."""
    for root, dirs, files in os.walk(path):
        for file in files:
            if file.endswith(".py"):
                file_path = os.path.join(root, file)
                # Compute path in zip
                rel_path = os.path.relpath(file_path, path)
                zip_path = os.path.join(prefix, rel_path)
                ziph.write(file_path, zip_path)

def main():
    print("=========================================================")
    print(" PACKAGING & DEPLOYING LAMBDAS")
    print("=========================================================")
    print(f"Profile: {PROFILE}")
    print(f"Region:  {REGION}\n")

    # Initialize client
    try:
        session = boto3.Session(profile_name=PROFILE, region_name=REGION)
    except Exception as e:
        print(f"Could not load profile '{PROFILE}', trying default session: {e}")
        session = boto3.Session(region_name=REGION)
        
    client = session.client("lambda")

    os.makedirs("dist", exist_ok=True)

    for info in LAMBDAS:
        zip_path = f"dist/{info['name']}.zip"
        print(f"[*] Packaging {info['name']}...")
        print(f"    - Main handler: {info['entry']} -> {info['zip_name']}")
        
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # 1. Write the main handler script
            zipf.write(info['entry'], info['zip_name'])
            
            # 2. Write the shared lakehouse library
            zip_dir("src/lakehouse", zipf, prefix="lakehouse")
            
        print(f"    - Uploading to AWS Lambda...")
        with open(zip_path, "rb") as f:
            zip_bytes = f.read()
            
        try:
            response = client.update_function_code(
                FunctionName=info['name'],
                ZipFile=zip_bytes
            )
            print(f"    - Success! Function version: {response.get('Version')}\n")
        except Exception as e:
            print(f"    - Error deploying {info['name']}: {e}\n")

    print("=========================================================")
    print(" Lambda deployment completed.")
    print("=========================================================")

if __name__ == "__main__":
    main()
