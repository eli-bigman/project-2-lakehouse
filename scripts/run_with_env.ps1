# scripts/run_with_env.ps1
# Loads environment variables from .env in the project root at the process level
# and runs the specified command within the same process context.
# Prevents ambient AWS credentials from shadowing the project credentials.

$ErrorActionPreference = "Stop"

# Locate the .env file in the project root (parent of scripts directory)
$ParentDir = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $ParentDir ".env"

if (Test-Path $EnvFile) {
    # Read the .env file and set the process-level environment variables
    Get-Content $EnvFile | ForEach-Object {
        $line = $_.Trim()
        # Process non-empty lines that are not comments and contain "="
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $key, $value = $line.Split("=", 2)
            $key = $key.Trim()
            $value = $value.Trim().Trim('"').Trim("'")
            if ($key) {
                [System.Environment]::SetEnvironmentVariable($key, $value, [System.EnvironmentVariableTarget]::Process)
            }
        }
    }
} else {
    Write-Warning ".env file not found at $EnvFile"
}

# Run the command with its arguments
if ($args) {
    $command = $args[0]
    $commandArgs = $args[1..($args.Length-1)]
    
    if ($commandArgs) {
        & $command $commandArgs
    } else {
        & $command
    }
    
    exit $LASTEXITCODE
}
