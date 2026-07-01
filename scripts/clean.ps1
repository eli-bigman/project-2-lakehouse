# scripts/clean.ps1
# PowerShell script orchestrating full teardown of AWS resources and local cleanup.
# Run with: powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\clean.ps1

$ErrorActionPreference = "Stop"

Write-Host "=========================================================" -ForegroundColor Red
Write-Host " STARTING E-COMMERCE LAKEHOUSE ARCHITECTURE TEARDOWN"
Write-Host "=========================================================" -ForegroundColor Red

# 1. AWS Teardown via python scripts/demo_teardown.py
Write-Host "`n[*] STEP 1/2: Wiping S3 buckets and destroying AWS resources..." -ForegroundColor Yellow
python scripts/demo_teardown.py
if ($LASTEXITCODE -ne 0) {
    Write-Error "AWS teardown script failed with exit code $LASTEXITCODE"
    exit $LASTEXITCODE
}
Write-Host "[OK] AWS resources successfully destroyed." -ForegroundColor Green

# 2. Local Cleanup
Write-Host "`n[*] STEP 2/2: Cleaning local cache and build artifacts..." -ForegroundColor Yellow

$cleanPaths = @(
    "dist",
    "build",
    ".pytest_cache"
)

foreach ($path in $cleanPaths) {
    if (Test-Path $path) {
        Write-Host "Removing directory: $path"
        Remove-Item -Recurse -Force $path
    }
}

# Recursively find and remove __pycache__ and *.egg-info
Write-Host "Searching for __pycache__ and egg-info directories..."
Get-ChildItem -Path . -Filter "__pycache__" -Recurse -Directory -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "Removing: $_"
    Remove-Item -Recurse -Force $_.FullName
}

Get-ChildItem -Path . -Filter "*.egg-info" -Recurse -Directory -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "Removing: $_"
    Remove-Item -Recurse -Force $_.FullName
}

Write-Host "[OK] Local cleanup complete." -ForegroundColor Green

Write-Host "`n=========================================================" -ForegroundColor Green
Write-Host " TEARDOWN COMPLETE! All resources cleaned." -ForegroundColor Green
Write-Host "=========================================================" -ForegroundColor Green
