# scripts/help.ps1
# Generates a beautiful, fast ANSI CLI menu for the E-Commerce Lakehouse Architecture project.
# Must run in a single PowerShell process to avoid output lag.

$esc = [char]27
$reset = "$esc[0m"
$bold = "$esc[1m"
$dim = "$esc[2m"
$italic = "$esc[3m"

# Colors
$green = "$esc[32m"
$yellow = "$esc[33m"
$blue = "$esc[34m"
$magenta = "$esc[35m"
$cyan = "$esc[36m"
$white = "$esc[37m"

# Environment Snapshot
$envName = $env:TF_ENV
if (-not $envName) { $envName = "dev" }
$region = $env:AWS_REGION
if (-not $region) { $region = "eu-west-1" }
$profile = $env:AWS_PROFILE
if (-not $profile) { $profile = "sandbox-lakehouse-dev" }

# Build the banner using standard ASCII to prevent encoding/BOM corruption in PowerShell 5.1
$banner = @"
${cyan}+--------------------------------------------------------------------------+
|                                                                          |
|       ${bold}E - C O M M E R C E   L A K E H O U S E   A R C H I T E C T U R E${reset}${cyan}       |
|       ${dim}AWS S3 + Lambda + Glue + Step Functions + DynamoDB + Athena${reset}${cyan}        |
|                                                                          |
+--------------------------------------------------------------------------+${reset}

Environment: ${green}$envName${reset}      Region: ${yellow}$region${reset}      Profile: ${cyan}$profile${reset}

${bold}${blue}LIFECYCLE${reset}
${cyan}+-----------------------+---------------------------------------------------+${reset}
| ${yellow}make clean${reset}            | Destroy all AWS resources + clean local artifacts |
| ${green}make deploy${reset}           | Spin up infrastructure, compile & upload code     |
${cyan}+-----------------------+---------------------------------------------------+${reset}

${bold}${blue}DATA${reset}
${cyan}+-----------------------+---------------------------------------------------+${reset}
| ${cyan}make seed-data${reset}        | Upload sample orders/products/items to trigger ETL|
| ${cyan}make query${reset}            | Run queries on Athena tables (row counts)         |
| ${cyan}make query-sample${reset}     | Fetch 5 sample records from Athena tables         |
| ${cyan}make ledger${reset}           | Scan DynamoDB Ingestion Ledger metadata           |
| ${cyan}make watermarks${reset}       | Scan DynamoDB Watermarks metadata                 |
${cyan}+-----------------------+---------------------------------------------------+${reset}

${bold}${blue}DEVELOPMENT${reset}
${cyan}+-----------------------+---------------------------------------------------+${reset}
| ${magenta}make ui${reset}               | Launch Streamlit validation dashboard :8501       |
| ${magenta}make test${reset}             | Run unit & integration test suites                |
| ${magenta}make fmt${reset}              | Format python code using black & isort            |
| ${magenta}make lint${reset}             | Lint python code using flake8 & black/isort checks|
${cyan}+-----------------------+---------------------------------------------------+${reset}

Demo flow:  ${yellow}make clean${reset}  ->  ${green}make deploy${reset}  ->  ${cyan}make seed-data${reset}  ->  ${cyan}make query${reset}  ->  ${magenta}make ui${reset}
"@

# Print the entire output string at once to prevent lag
[Console]::Out.WriteLine($banner)
