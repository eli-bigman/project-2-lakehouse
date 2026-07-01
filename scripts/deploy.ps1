# scripts/deploy.ps1
# PowerShell script orchestrating the full infrastructure and code deployment.
# Run with: powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\deploy.ps1

$ErrorActionPreference = "Stop"

# Use environment variables loaded from .env (e.g. TF_ENV, AWS_REGION, S3_ARTIFACTS_BUCKET)
$envName = $env:TF_ENV
if (-not $envName) { $envName = "dev" }
$region = $env:AWS_REGION
if (-not $region) { $region = "eu-west-1" }
$artifactsBucket = $env:S3_ARTIFACTS_BUCKET
if (-not $artifactsBucket) { $artifactsBucket = "ecom-lakehouse-artifacts-dev" }

Write-Host "=========================================================" -ForegroundColor Cyan
Write-Host " STARTING E-COMMERCE LAKEHOUSE ARCHITECTURE DEPLOYMENT"
Write-Host " Env: $envName | Region: $region | Artifacts: $artifactsBucket"
Write-Host "=========================================================" -ForegroundColor Cyan

# 1. Terraform Infrastructure Provisioning
Write-Host "`n[*] STEP 1/4: Provisioning AWS Infrastructure via Terraform..." -ForegroundColor Yellow
$parentDir = Split-Path -Parent $PSScriptRoot
$infraDir = Join-Path (Join-Path $parentDir "infra\envs") $envName

if (-not (Test-Path $infraDir)) {
    Write-Error "Terraform environment directory not found: $infraDir"
    exit 1
}

# Run terraform init and apply
Push-Location $infraDir
try {
    Write-Host "Running terraform init..."
    terraform init
    Write-Host "Running terraform apply..."
    terraform apply -var-file="$envName.tfvars" -auto-approve
} finally {
    Pop-Location
}
Write-Host "[OK] Terraform provisioning complete." -ForegroundColor Green

# 2. Deploy Lambda Functions
Write-Host "`n[*] STEP 2/4: Packaging and Deploying Lambdas..." -ForegroundColor Yellow
python scripts/deploy_lambdas.py
if ($LASTEXITCODE -ne 0) {
    Write-Error "Lambda deployment failed with exit code $LASTEXITCODE"
    exit $LASTEXITCODE
}
Write-Host "[OK] Lambdas successfully updated." -ForegroundColor Green

# 3. Build & Upload Spark Wheel and Glue scripts
Write-Host "`n[*] STEP 3/4: Building and Uploading PySpark Wheel & Glue scripts..." -ForegroundColor Yellow

# Build Python Wheel
Write-Host "Building wheel locally..."
if (Test-Path "dist") { Remove-Item -Recurse -Force "dist" }
pip wheel --no-deps -w dist/ .
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to build Spark library wheel"
    exit $LASTEXITCODE
}

# Find built wheel file
$wheels = Get-ChildItem dist/*.whl
if (-not $wheels) {
    Write-Error "No wheel found in dist/ directory after pip wheel"
    exit 1
}
$wheelPath = $wheels[0].FullName
Write-Host "Found wheel: $($wheels[0].Name)"

# Upload to S3
Write-Host "Uploading wheel to s3://$artifactsBucket/wheels/lakehouse-latest.whl..."
aws s3 cp $wheelPath "s3://$artifactsBucket/wheels/lakehouse-latest.whl"
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to upload wheel to S3"
    exit $LASTEXITCODE
}

Write-Host "Uploading Glue scripts..."
aws s3 cp src/glue_jobs/ingest.py "s3://$artifactsBucket/scripts/ingest.py"
aws s3 cp src/glue_jobs/optimize.py "s3://$artifactsBucket/scripts/optimize.py"
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to upload Glue scripts to S3"
    exit $LASTEXITCODE
}
Write-Host "[OK] Spark wheel and Glue scripts staged in S3." -ForegroundColor Green

# 4. Clean Slate (Reset data zones for Day-0 state)
Write-Host "`n[*] STEP 4/4: Resetting S3 data zones & DynamoDB Ledger (Clean Slate)..." -ForegroundColor Yellow
python scripts/clean_slate.py
if ($LASTEXITCODE -ne 0) {
    Write-Error "Clean slate reset failed"
    exit $LASTEXITCODE
}
Write-Host "[OK] Environment reset to day-zero state." -ForegroundColor Green

Write-Host "`n=========================================================" -ForegroundColor Green
Write-Host " DEPLOYMENT COMPLETE! Ready for demo." -ForegroundColor Green
Write-Host " Run 'make seed-data' to trigger the ingestion pipeline."
Write-Host "=========================================================" -ForegroundColor Green
