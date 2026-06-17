# set_aws_profile.ps1
# Reads AWS credentials from .env and writes them to the sandbox-lakehouse-dev profile.
# Run from project root: .\scripts\set_aws_profile.ps1

$envFile = Join-Path $PSScriptRoot ".." ".env"
$profile = "sandbox-lakehouse-dev"

$creds = @{}
foreach ($line in Get-Content $envFile) {
    if ($line -match "^(?:export\s+)?(AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY)=(.+)$") {
        $creds[$Matches[1]] = $Matches[2].Trim()
    }
}

if ($creds.Count -ne 2) {
    Write-Error "Could not find AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in .env"
    exit 1
}

aws configure set aws_access_key_id     $creds["AWS_ACCESS_KEY_ID"]     --profile $profile
aws configure set aws_secret_access_key $creds["AWS_SECRET_ACCESS_KEY"] --profile $profile
aws configure set region eu-west-1                                       --profile $profile
# Clear any stale session token from a previous assumed-role session
aws configure set aws_session_token "" --profile $profile

Write-Host "Credentials written to profile: $profile"
Write-Host ""
aws sts get-caller-identity --profile $profile
