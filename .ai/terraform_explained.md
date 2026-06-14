# Terraform Explained — From First Principles to This Project

Terraform is the infrastructure-as-code tool used to create and manage every AWS
resource in this project. This document explains what Terraform is, how it works, and
then walks through exactly how it is used here — every file, every pattern, every
decision — in a way that assumes you have not worked with it before.

---

## What Terraform Is

When you build something on AWS, you need AWS resources — S3 buckets, Lambda functions,
IAM roles, DynamoDB tables, and so on. The naive way to create these is to click through
the AWS console. The problem is that what you clicked is not recorded anywhere. If you
need to recreate the same setup in a different account, or in six months after you forgot
what you did, or after something accidentally gets deleted, you have no reliable way to
do it.

Terraform solves this by letting you describe your infrastructure in configuration files
written in a language called HCL (HashiCorp Configuration Language). These files live
in your git repository alongside your code. When you run Terraform, it reads those files,
compares them to what actually exists in AWS, and creates, updates, or deletes resources
to make reality match what you described. The configuration is the single source of truth
for your infrastructure.

This matters for three reasons:

**Reproducibility.** Anyone with the right AWS credentials and the config files can
recreate the same infrastructure. The CI/CD pipeline in this project does this
automatically on every push to main.

**Reviewability.** Infrastructure changes go through pull requests like code changes.
You can see exactly what is going to change before it happens.

**Safety.** Terraform tells you what it is going to do before it does it. You run
`terraform plan` to get a preview, then `terraform apply` to execute.

---

## The Core Terraform Workflow

Every Terraform project follows the same three commands:

**`terraform init`** — Downloads the providers (plugins that know how to talk to AWS),
sets up the backend (where state is stored), and initializes modules. You run this once
when you first check out the project, and again whenever the provider versions or backend
configuration changes.

**`terraform plan`** — Reads your config files, reads the current state of AWS, and
prints a diff showing exactly what will be created, modified, or destroyed. Nothing
changes in AWS when you run plan. It is a preview only. Always read the plan before
applying.

**`terraform apply`** — Executes the plan. Terraform makes the AWS API calls needed to
reach the desired state. After a successful apply, it writes the new state to the backend.
In CI/CD, this runs with `-auto-approve` to skip the interactive confirmation prompt.

There is a fourth command that matters here:

**`terraform destroy`** — The reverse of apply. Destroys all resources Terraform manages.
Used for teardown after testing.

---

## State — The Most Important Concept

Terraform keeps a record of everything it has created. This record is called the state
file (`terraform.tfstate`). The state is how Terraform knows what already exists so it
can calculate what needs to change on the next apply.

If you create an S3 bucket with Terraform and the state file says it exists, Terraform
will leave it alone on the next apply (assuming you did not change the config). If you
delete the bucket manually in the console without telling Terraform, the state still says
it exists — Terraform will try to create it again on the next apply.

The state is authoritative. You should never edit it manually.

**Remote state** is when the state file is stored somewhere shared rather than on one
developer's laptop. In this project, the state is stored in an S3 bucket:
`ecom-lakehouse-tf-state-970547336735`. Multiple people can run Terraform against the
same infrastructure without stepping on each other because the state is in a central
location rather than scattered across machines.

---

## State Locking

When two people run `terraform apply` at the same time against the same remote state,
they would both read the current state, calculate a plan, and then both try to write
updates — potentially overwriting each other's changes or producing corrupted state.

State locking prevents this. Before Terraform writes to the state, it acquires a lock.
If another process already holds the lock, Terraform waits rather than proceeding.

In this project, locking is done via a DynamoDB table called `ecom-lakehouse-tf-locks`.
Terraform writes a lock record to DynamoDB before it starts any apply, and deletes it
when the apply completes. If Terraform crashes without releasing the lock, you can force-
unlock it with `terraform force-unlock <lock-id>`.

This came up in practice during this project — a background apply command hung without
releasing the lock, and a `terraform force-unlock` was needed to unblock the CI pipeline.

---

## Providers

A provider is a plugin that teaches Terraform how to interact with a specific service.
This project uses two providers:

**hashicorp/aws** — Knows how to create, read, update, and delete AWS resources. Every
`aws_s3_bucket`, `aws_lambda_function`, `aws_iam_role`, and so on is handled by this
provider. The version is pinned to `>= 5.0` to ensure compatibility.

**hashicorp/archive** — A utility provider that creates ZIP files. It is used by the
Lambda module to create a placeholder ZIP package when Lambda functions do not have real
code yet. Without a ZIP, Terraform cannot create the Lambda function resource.

Providers are declared in `infra/envs/dev/providers.tf`. The `required_providers` block
pins versions. `terraform init` downloads the correct versions.

```hcl
terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = ">= 2.4"
    }
  }
}

provider "aws" {
  region = "eu-west-1"
  default_tags {
    tags = {
      Project     = "ecom-lakehouse"
      Environment = "dev"
      ManagedBy   = "terraform"
    }
  }
}
```

The `default_tags` block applies tags to every AWS resource created by this provider.
Instead of adding `tags = { Project = "ecom-lakehouse" }` to every single resource
definition, the provider applies them automatically. This is important for cost allocation
and for identifying which resources belong to this project when looking at the AWS console.

The `profile` line was intentionally omitted from the provider block. If it were set to
`sandbox-lakehouse-dev`, Terraform would only work on machines that have that AWS profile
configured — meaning CI/CD runners would fail because they do not have local AWS profiles.
Instead, Terraform picks up credentials from environment variables
(`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`) which is how the
GitHub Actions OIDC flow injects credentials. Locally, you set
`export AWS_PROFILE=sandbox-lakehouse-dev` before running Terraform.

---

## The Backend

The backend tells Terraform where to store the state file. This project uses S3 as the
backend, configured in `infra/envs/dev/backend.tf`:

```hcl
terraform {
  backend "s3" {
    bucket         = "ecom-lakehouse-tf-state-970547336735"
    key            = "env/dev/terraform.tfstate"
    region         = "eu-west-1"
    dynamodb_table = "ecom-lakehouse-tf-locks"
    encrypt        = true
  }
}
```

The `bucket` is the S3 bucket where the state file lives. The `key` is the path within
that bucket — `env/dev/terraform.tfstate`. This path structure means a `prod` environment
could store its state at `env/prod/terraform.tfstate` in the same bucket without
collision.

The `dynamodb_table` is the lock table described above.

`encrypt = true` means the state file is encrypted at rest using the bucket's default
KMS key. The state file can contain sensitive values (like IAM role ARNs and resource IDs)
so encryption is important.

The state bucket and DynamoDB lock table are NOT managed by Terraform. They need to
exist before Terraform can run. They were created manually (bootstrapped) before the
first `terraform init`. You cannot use Terraform to create the thing Terraform needs to
store its own state.

---

## Resources

A resource is the fundamental building block in Terraform. It represents one real thing
in AWS — one S3 bucket, one IAM role, one Lambda function, one DynamoDB table.

```hcl
resource "aws_s3_bucket" "raw" {
  bucket        = "ecom-lakehouse-raw-dev"
  force_destroy = false

  tags = {
    Name = "ecom-lakehouse-raw-dev"
    Zone = "raw"
  }

  lifecycle {
    prevent_destroy = true
  }
}
```

The first string after `resource` (`"aws_s3_bucket"`) is the resource type — this tells
Terraform which provider and which API to use. The second string (`"raw"`) is the local
name you give this resource within your Terraform config. Together they form the resource
address: `aws_s3_bucket.raw`.

Once a resource is created, you can reference its attributes elsewhere in the config
using this address. For example, `aws_s3_bucket.raw.id` gives you the bucket name, and
`aws_s3_bucket.raw.arn` gives you the bucket's ARN.

The `lifecycle` block with `prevent_destroy = true` tells Terraform to refuse to destroy
this resource even if `terraform destroy` is run. You would get an error like "Error: the
plan would destroy this resource, but it is protected by lifecycle.prevent_destroy."
To actually destroy it, you must first change the config to remove or override that block.

---

## Variables

Variables make your configuration reusable. Instead of hardcoding values that differ
between environments (account ID, email address, which AWS features to enable), you
define variables and pass the values in separately.

Variables are declared in `variables.tf`:

```hcl
variable "account_id" {
  description = "AWS account ID. Used to construct ARNs in IAM policies."
  type        = string
}

variable "protect_stateful" {
  description = "When true, stateful buckets cannot be destroyed."
  type        = bool
  default     = true
}
```

A variable with no `default` is required — Terraform will ask you for it interactively
if you do not supply it another way.

Values are supplied in a `.tfvars` file. In this project, `dev.tfvars` contains:

```hcl
account_id       = "970547336735"
alert_email      = "richard.nutsugah@amalitechtraining.org"
github_org       = "eli-bigman"
github_repo      = "project-2-lakehouse"
protect_stateful = true
```

You pass this file to Terraform: `terraform apply -var-file="dev.tfvars"`.

In CI/CD, secrets like the account ID and email cannot be in a committed file. They are
passed as environment variables prefixed with `TF_VAR_`:

```
TF_VAR_account_id=970547336735
TF_VAR_alert_email=richard.nutsugah@amalitechtraining.org
```

Terraform automatically picks up any environment variable that starts with `TF_VAR_` and
uses it as the value for the corresponding variable. This is how the deploy workflow
passes values without committing them to the repo.

The `dev.tfvars` file is gitignored because it may contain email addresses and account
IDs that should not be public. `dev.tfvars.example` is committed as a template.

---

## Outputs

Outputs expose values from your Terraform configuration to the outside world. After a
successful apply, Terraform prints all output values. Scripts and pipelines can also
read them programmatically.

```hcl
output "state_machine_arn" {
  description = "ARN of the Step Functions state machine."
  value       = module.stepfunctions.state_machine_arn
}

output "gha_deploy_role_arn" {
  description = "ARN for GitHub Actions OIDC role."
  value       = module.iam.gha_deploy_role_arn
}
```

In this project, outputs at the env level (`infra/envs/dev/main.tf`) expose the resource
ARNs and names that operators need after deploying — the state machine ARN to trigger
pipeline runs, the raw bucket name to upload test data, the Glue job name to monitor.

---

## Modules

A module is a directory of Terraform files that can be called like a function. You pass
inputs (variables), it creates resources, and it exposes outputs. Modules let you
organize infrastructure into reusable components with clear boundaries.

This project has seven modules:

```
infra/modules/
├── s3_zones/       creates all S3 buckets and bucket policies
├── dynamodb/       creates the ingestion ledger and watermarks tables
├── iam/            creates all IAM roles and policies
├── glue/           creates the Glue database, table definitions, and job
├── lambda/         creates all Lambda functions
├── stepfunctions/  creates the Step Functions state machine
└── observability/  creates SNS, CloudWatch, EventBridge rules, and the dashboard
```

The environment directory (`infra/envs/dev/`) is itself a module — it calls all seven
modules and wires them together. This is the "composition" layer.

Calling a module looks like this:

```hcl
module "dynamodb" {
  source = "../../modules/dynamodb"

  env         = "dev"
  prefix      = "ecom-lakehouse"
  use_cmk     = false
  kms_key_arn = ""
}
```

`source` is the path to the module directory. Everything else is a variable being passed
in. After this block runs, you can access the module's outputs via
`module.dynamodb.ledger_table_arn`, `module.dynamodb.watermarks_table_name`, etc.

---

## How Modules Talk to Each Other — The Dependency Graph

Modules cannot run in arbitrary order. If IAM needs the S3 bucket ARNs to write policies,
the S3 buckets must exist before the IAM roles. Terraform figures out this ordering
automatically by following references.

If the IAM module variable `raw_bucket_arn` is set to `module.s3_zones.raw_bucket_arn`,
Terraform knows it must create the S3 zones module first, because the IAM module depends
on its output.

In `infra/envs/dev/main.tf`, the dependency chain is:

```
s3_zones → (ARNs)   → iam
dynamodb → (ARNs)   → iam
observability → (sns_topic_arn) → iam
iam → (role ARNs) → glue, lambda
glue, lambda, observability → (ARNs) → stepfunctions
```

There was one tricky cycle that had to be broken: observability needs to know the Glue
job names to create the EventBridge failure alert rule. But Glue needs IAM, and IAM needs
observability's SNS topic ARN. That would create a circular dependency:
`observability → glue → iam → observability`.

The solution was to compute the Glue job name as a local variable in `main.tf` using the
same naming convention the Glue module uses internally, rather than reading it from the
Glue module's output. The observability module gets the constructed string; it does not
depend on the Glue module at all. The Glue module still creates the job with the matching
name, but the dependency arrow is broken.

```hcl
locals {
  glue_ingest_job_name = "${local.prefix}-ingest-${local.env}"
}

module "observability" {
  glue_job_names = [local.glue_ingest_job_name]  # local, not module.glue.*
}
```

---

## Data Sources

A data source reads information from AWS without creating anything. It is the read-only
equivalent of a resource.

The most common use in this project is `data "aws_region" "current" {}` — this reads
the current region from the provider configuration so it can be used in ARN strings
without hardcoding `eu-west-1`:

```hcl
data "aws_region" "current" {}

resource "aws_cloudwatch_metric_alarm" "stepfunctions_failed" {
  dimensions = {
    StateMachineArn = "arn:aws:states:${data.aws_region.current.name}:${var.account_id}:..."
  }
}
```

Without this, every ARN in every module would have the region hardcoded as a string. If
the project moves to a different region (which happened — from us-east-1 to eu-west-1 for
the sandbox), every hardcoded string would need to be updated. The data source makes the
modules region-agnostic.

---

## `for_each` — Creating Multiple Similar Resources

When you need several resources that differ only in their configuration values, `for_each`
lets you iterate over a map or set rather than copy-pasting resource blocks.

The stateless S3 buckets (staging, athena-results, artifacts) are created this way in
`infra/modules/s3_zones/stateless.tf`:

```hcl
locals {
  stateless_zones = {
    "staging"        = { expiry_days = 7 }
    "athena-results" = { expiry_days = 30 }
    "artifacts"      = { expiry_days = 0 }
  }
}

resource "aws_s3_bucket" "stateless" {
  for_each = local.stateless_zones
  bucket   = "${var.prefix}-${each.key}-${var.env}"
}
```

This creates three buckets. Each one is addressed as `aws_s3_bucket.stateless["staging"]`,
`aws_s3_bucket.stateless["athena-results"]`, and `aws_s3_bucket.stateless["artifacts"]`.

The reason `for_each` is NOT used for the stateful buckets (raw, dwh, archive, quarantine)
is important to understand. If you rename a key in a `for_each` map — say, renaming
`"raw"` to `"raw-landing"` — Terraform sees the old key as deleted and the new key as
new. It plans to destroy `aws_s3_bucket.stateless["raw"]` and create
`aws_s3_bucket.stateless["raw-landing"]`. For a bucket holding production data, this is
catastrophic. The stateful buckets use individual resource blocks to make this mistake
impossible, combined with `lifecycle { prevent_destroy = true }`.

---

## `lifecycle` Blocks

The `lifecycle` block controls how Terraform manages the lifecycle of a resource beyond
the normal create/update/destroy cycle.

**`prevent_destroy = true`**: Terraform refuses to destroy this resource. Used on all
stateful S3 buckets. Attempting `terraform destroy` will error:

```
Error: Instance cannot be destroyed
  Resource aws_s3_bucket.raw has lifecycle.prevent_destroy set, but the plan
  calls for this resource to be destroyed. To avoid this error and continue,
  add the "prevent_destroy = false" argument.
```

**`force_destroy = !var.protect_stateful`** (on the `aws_s3_bucket` resource itself,
not inside lifecycle): S3 buckets cannot be deleted if they contain objects. Setting
`force_destroy = true` tells Terraform to empty the bucket before deleting it. Combined
with `prevent_destroy`, the teardown procedure is:

1. Set `protect_stateful = false` in `dev.tfvars`
2. Run `terraform apply` — this changes `force_destroy` to `true` on all stateful buckets
   but changes nothing in AWS yet
3. Run `terraform destroy` — now Terraform can empty and delete the stateful buckets

This two-step process means you cannot accidentally destroy the data buckets by running
`terraform destroy` without first explicitly overriding the protection.

---

## `locals`

Local values are named expressions computed within a Terraform file. They are not
variables (cannot be overridden from outside) and are not outputs (cannot be read by
other modules). They are internal aliases for computed values.

In `infra/envs/dev/main.tf`:

```hcl
locals {
  env    = "dev"
  prefix = "ecom-lakehouse"

  # Constructed job names to break module dependency cycles
  glue_ingest_job_name = "${local.prefix}-ingest-${local.env}"
  state_machine_name   = "${local.prefix}-sm-${local.env}"
}
```

Using `local.env` throughout means if you ever need to change the environment name,
you change it in one place. It also makes the naming convention explicit and readable.

---

## The Lock File — `.terraform.lock.hcl`

After `terraform init`, Terraform writes a `.terraform.lock.hcl` file. This file records
the exact versions of providers that were downloaded:

```hcl
provider "registry.terraform.io/hashicorp/aws" {
  version     = "6.50.0"
  constraints = ">= 5.0.0"
  hashes = [
    "h1:...",
  ]
}
```

This file is committed to git. Its purpose is to make sure that everyone who runs
`terraform init` — including CI/CD — gets exactly the same provider version. The
`constraints` say which versions are allowed (>= 5.0.0), and the lock file records
which specific version was actually selected (6.50.0). Without the lock file, different
machines might download different versions, leading to subtle behavior differences.

When you want to upgrade a provider, you run `terraform init -upgrade`, which resolves
new versions against the constraints and updates the lock file. You then commit the
updated lock file.

---

## The `.terraform/` Directory

When you run `terraform init`, it creates a hidden `.terraform/` directory in the working
directory. This directory contains the downloaded provider binaries and the cached modules.
It is always gitignored. There is no reason to commit it — `terraform init` regenerates it
from the lock file and the configuration.

---

## What Runs When — The Full Sequence

To deploy this project's infrastructure from scratch:

1. **Bootstrap** (one time only, done manually):
   - Create the state S3 bucket (`ecom-lakehouse-tf-state-970547336735`)
   - Enable versioning and encryption on that bucket
   - Create the DynamoDB lock table (`ecom-lakehouse-tf-locks`)

2. **`terraform init`** (in `infra/envs/dev/`):
   - Downloads provider plugins (aws, archive)
   - Configures the S3 backend (reads state from the state bucket)
   - Initializes module paths

3. **`terraform plan -var-file=dev.tfvars`**:
   - Reads all `.tf` files
   - Reads current state from S3
   - Calls AWS APIs to check what actually exists
   - Prints a diff: what will be created, changed, or destroyed

4. **`terraform apply -var-file=dev.tfvars`**:
   - Executes the plan
   - Creates ~86 AWS resources in dependency order
   - Writes updated state to S3
   - Prints output values (bucket names, ARNs, etc.)

5. After testing, **teardown**:
   - `terraform apply -var-file=dev.tfvars -var="protect_stateful=false"` — disables prevent_destroy
   - `terraform destroy -var-file=dev.tfvars -var="protect_stateful=false"` — destroys everything

---

## How CI/CD Uses Terraform

The deploy workflow (`.github/workflows/deploy.yml`) runs on every push to main:

```yaml
- name: Terraform apply (dev)
  working-directory: infra/envs/dev
  env:
    TF_VAR_account_id: ${{ secrets.AWS_ACCOUNT_ID }}
    TF_VAR_alert_email: ${{ secrets.ALERT_EMAIL }}
    TF_VAR_github_org: eli-bigman
    TF_VAR_github_repo: project-2-lakehouse
  run: |
    terraform init
    terraform apply -auto-approve
```

There is no `-var-file` flag here because the values are passed via `TF_VAR_*` environment
variables. The AWS credentials come from the OIDC step earlier in the workflow — by the
time this step runs, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and
`AWS_SESSION_TOKEN` are already set as environment variables, which the AWS provider
picks up automatically (no profile needed).

`-auto-approve` skips the interactive "Do you want to perform these actions? yes/no"
prompt. This is necessary in CI because there is no human to type yes.

On the first run against a fresh environment, this creates all 86 resources. On
subsequent runs, it is a no-op if nothing in the configuration changed. If a module was
updated (say, a new IAM policy added), Terraform applies only the difference.

---

## Common Things That Go Wrong

**"failed to get shared config profile"** — This happened in this project when
`profile = "sandbox-lakehouse-dev"` was set in `providers.tf`. CI runners do not have
local AWS profiles. The fix was to remove the profile line and rely on environment
variables.

**"Error acquiring the state lock"** — Two Terraform processes tried to run at the same
time (a local apply and a CI apply). One held the DynamoDB lock. The fix is to wait for
the first to complete, or run `terraform force-unlock <lock-id>` if the process that
held the lock has died without releasing it.

**"Instance cannot be destroyed: lifecycle.prevent_destroy"** — You ran `terraform destroy`
on a stateful bucket without first setting `protect_stateful=false`. The fix is to follow
the documented teardown procedure.

**"Error: Reference to undeclared module"** — A typo in a module reference. Check the
module name spelling in `main.tf` vs the module directory name.

**"No value for required variable"** — A variable with no default was not supplied via
`-var-file`, `TF_VAR_*`, or interactive input. Check that `dev.tfvars` is passed in the
command, or that all required `TF_VAR_*` environment variables are set.

---

## The Directory Structure in This Project

```
infra/
├── envs/
│   └── dev/
│       ├── backend.tf     — remote state configuration (S3 + DynamoDB lock)
│       ├── providers.tf   — AWS + archive provider, version requirements, default_tags
│       ├── variables.tf   — declared variables (account_id, alert_email, etc.)
│       ├── main.tf        — calls all seven modules and wires them together
│       ├── dev.tfvars     — actual variable values (gitignored)
│       └── dev.tfvars.example  — template (committed)
└── modules/
    ├── s3_zones/          — S3 buckets and bucket policies
    │   ├── stateful.tf    — raw, dwh, archive, quarantine (individual resources)
    │   ├── stateless.tf   — staging, athena-results, artifacts (for_each)
    │   ├── variables.tf   — env, prefix, use_cmk, kms_key_arn, protect_stateful
    │   └── outputs.tf     — bucket names and ARNs
    ├── dynamodb/          — ingestion ledger + watermarks tables
    ├── iam/               — all IAM roles and policies
    ├── glue/              — Glue database, table DDL, Glue job
    ├── lambda/            — Lambda functions (normalize, claim, archive, validate-schema)
    ├── stepfunctions/     — Step Functions state machine
    └── observability/     — SNS, CloudWatch log group, EventBridge rule, dashboard, alarm
```

Each module has a `variables.tf` (what it accepts), a `main.tf` (what it creates), and
an `outputs.tf` (what it exposes). This three-file pattern makes every module self-
contained and easy to reason about in isolation.
