# Interview Q&A — ecom-lakehouse Project

These are the questions a senior data engineer would ask about this project, with the
answers you should be able to give. Read these until the answers feel natural, not
memorised. The goal is to be able to explain any of these in your own words.

---

## Section 1 — Architecture and the Big Picture

**Q: Walk me through what this system does from the moment a file lands in S3 to when
it is queryable in Athena.**

A file lands in the raw S3 bucket under a path like
`fct_orders/2025/04/orders_apr_2025.xlsx`. An EventBridge rule (or a manual trigger) starts
a Step Functions execution. The first thing that happens is a Lambda function claims the
file — it writes a record to the DynamoDB ingestion ledger saying "this batch is now
CLAIMED", using a conditional write so that if the same file is already claimed, the
workflow stops rather than processing it twice.

A second Lambda (the normalizer) then reads the raw file. If it is an Excel file, pandas
and openpyxl convert it to Parquet with the canonical column names and types defined in
the Design Contract. The Parquet file goes to the staging bucket.

AWS Glue then runs a Spark job parameterized for that specific dataset. The Spark job reads
the staged Parquet, runs validation rules (no null primary keys, valid timestamps, valid
foreign key references), separates clean records from rejected ones, computes a SHA-256
hash for deduplication, and executes a Delta MERGE on the DWH bucket — matching incoming
rows to existing rows on the natural key. Clean records land in the Delta table; rejected
records go to the quarantine bucket with a reason code. The DynamoDB ledger is updated to
LOADED with the row counts.

After a successful Glue run, an archive Lambda copies the original raw file to the archive
bucket and marks the ledger entry as ARCHIVED.

The Glue Data Catalog already has the Delta table registered as `table_type=DELTA`. Athena
reads the Delta transaction log directly. The data is immediately queryable.

---

**Q: What is the medallion architecture and how does it apply here?**

The medallion architecture is a pattern that organises data into layers, each one cleaner
and more structured than the previous. The names Bronze, Silver, and Gold are common
shorthand.

In this project: Bronze is the raw and staging zones — raw holds the original files
exactly as received, and staging holds the normalised Parquet before any business
transformation. Silver is the DWH zone — Delta tables with ACID guarantees, validated
records, deduplication applied, schema enforced. Gold is the Athena query layer —
analytics-ready views or aggregated marts on top of the Silver Delta tables.

The key property of this pattern is that each layer is independently recoverable. If
something goes wrong in the Silver write, the Bronze data is still intact and you can
re-run the transformation. You never delete the original.

---

**Q: Why did you use Delta Lake instead of just writing Parquet files?**

Plain Parquet gives you columnar storage and good compression, but it has no concept of
transactions. If a Glue job writes 100 files and crashes after 50, you end up with a
partially-written dataset. There is no way to know which files are from the complete write
and which are the partial one. Queries will silently return wrong results.

Delta Lake wraps Parquet with a transaction log. Every write is recorded as a versioned
commit. Either the entire write succeeds and is committed, or it is rolled back — there is
no partial state visible to readers. On top of that, Delta gives you MERGE (which is how
deduplication works), schema enforcement (bad data does not silently corrupt the table),
and time travel (you can query the table as it was at any previous version).

The brief also explicitly mandated Delta Lake, so it was not optional — but even without
that mandate, it is the right choice for a production system that requires reliability.

---

**Q: You have three datasets. Why does each one get its own Glue job instead of
processing them all in a single Spark session?**

The brief explicitly says "Run a Glue Job for each dataset." That is a hard requirement.

But beyond compliance, per-dataset jobs are operationally better. If the orders file has
a problem that causes the Spark job to fail, the products and order items jobs are
unaffected. Each dataset can be retried independently. The Step Functions state machine
branches per dataset, so the failure handling is granular — you can alert on orders
failing without treating a products failure as the same event.

There was a suggestion during architectural review to consolidate all three into a single
Spark session for efficiency. We rejected it because it violates the explicit requirement
and creates a shared failure domain. We documented it as an optional optimisation for when
monthly volume grows significantly, but it is not the default.

The implementation uses one parameterized Glue job definition — the same job code handles
all three datasets, selected via a `dataset` parameter. The Step Functions state machine
invokes it three times with different parameters. So there is one job definition but three
invocations per pipeline run.

---

**Q: What is the DynamoDB control plane doing? Why is it there?**

It solves two problems that S3 alone cannot solve: idempotency at the file level, and
watermarking.

For idempotency: S3 gives you no way to do a conditional "process this file only if it has
not already been processed." S3 events can fire more than once for the same object (S3
event delivery is at-least-once, not exactly-once). If the same file lands twice, or if
an S3 event fires twice, you do not want to run the pipeline twice. The DynamoDB ingestion
ledger uses a conditional write — the Lambda claims the file only if no record with that
batch ID and dataset already exists. DynamoDB's conditional writes are strongly consistent.
If two Lambda invocations race to claim the same file, exactly one wins.

For watermarking: the watermarks table records the last successfully processed batch
timestamp per dataset. This is used to identify which files have already been handled and
to resume from the last known-good state after a failure.

The DynamoDB tables also give you visibility. You can query the ledger to see every file
ever processed, its status, and exactly how many rows were clean versus rejected.

---

**Q: What does the quarantine zone do and why quarantine instead of failing the whole run?**

If a validation rule fails — say, a row has a null `order_id` — that row goes to the
quarantine bucket instead of being written to the Delta table. The run continues with the
remaining valid records.

The alternative is fail-fast: stop the entire run if any record fails validation. Fail-
fast is appropriate when the data is so bad that the partial result would be meaningless.
But for this use case — monthly e-commerce transaction files — one bad row in 500 orders
should not prevent the other 499 from being available for analysis. The business still
needs to see its data.

Quarantine gives you both things: the valid data loads, and you have a complete audit trail
of every rejected record with the reason it was rejected. The ledger records the reject
count, and if the reject rate exceeds a configured threshold (the default is 5%), the run
is flagged as a warning even though it technically completed.

---

**Q: How does deduplication work across files? If the same order appears in two different
monthly files, what happens?**

Two layers work together.

Within a single file: before writing to Delta, a SHA-256 hash is computed over all the
business columns of each row. Within the same incoming batch, if two rows produce the same
hash (identical data), only one is kept.

Across files (the more important case): the Glue job executes a Delta MERGE. The MERGE
statement matches incoming rows to existing rows in the Delta table on the natural key —
`order_id` for orders, `product_id` for products, `(order_id, product_id)` for order
items. If a match exists, the existing row is updated. If no match exists, the new row is
inserted. If the same `order_id` is in two different monthly files, the second run simply
updates the row rather than inserting a duplicate.

This is the core of why Delta MERGE is valuable. Running the pipeline twice on the same
file produces the same result as running it once — that is the definition of idempotency.

---

## Section 2 — AWS Services

**Q: Why Lambda for normalization instead of a Glue Python-shell job?**

Cost and startup time. A Glue Python-shell job costs a minimum of one DPU-minute per
run — that is one compute unit for one full minute, billed at the Glue DPU-hour rate of
roughly $0.44 per DPU-hour. For a Lambda that converts a 1 MB Excel file to Parquet in
under a second, that is paying for 59 seconds of idle compute.

Lambda bills in milliseconds and at a much lower rate. For files of this size, Lambda is
roughly 95% cheaper. More importantly, the brief constrains Glue + Spark to the ETL work
— the validation, transformation, and Delta MERGE. It does not constrain the file format
conversion step. Lambda is the right tool for a fast, cheap, stateless conversion.

---

**Q: Why Step Functions Standard and not Express?**

Express workflows have a maximum execution duration of 5 minutes. A Glue job that
processes 2,768 order items with a two-worker Spark cluster might take 3-4 minutes alone.
The full pipeline — claim, normalize, validate schema, run Glue, archive — will regularly
exceed 5 minutes. Express is a hard non-starter on duration alone.

Express workflows also use at-least-once execution semantics, meaning a state can execute
more than once. For a workflow that runs a Glue MERGE job, at-least-once execution could
trigger two concurrent Glue runs for the same file — and depending on timing, they could
both be writing to the same Delta partition simultaneously. Standard workflows have exactly-
once semantics: each state executes exactly once per execution.

Standard costs more per state transition, but this pipeline runs monthly and has few
transitions. The total cost difference is negligible.

---

**Q: Athena reads Delta natively — what does that mean and why does it matter?**

Older versions of the Delta-on-Athena setup required generating symlink manifest files.
When a Delta MERGE ran, you also had to call `GENERATE symlink_format_manifest` to write
a manifest file listing all the Parquet files that make up the current table version.
Athena would read those manifests instead of the Delta transaction log directly. If you
forgot to generate the manifests, Athena would read stale data.

Amazon Athena v3 (which this project uses) reads the Delta transaction log directly. You
register the table in the Glue Data Catalog with `table_type=DELTA` and point it at the
S3 path where the Delta table lives. Athena reads `_delta_log/*.json` files to understand
the current state of the table — which Parquet files are active, what the schema is, what
partitions exist. There are no manifest files to generate, no `MSCK REPAIR TABLE` to run,
no risk of Athena reading stale data because someone forgot a step.

This simplifies the pipeline significantly. A Glue job writes to Delta, and Athena
immediately sees the new data — no extra steps.

---

**Q: What is an S3 bucket policy versus an IAM role policy? How are they different?**

Both control access to S3, but they are evaluated differently and serve different purposes.

An IAM role policy is attached to an identity — a user, role, or Lambda function. It says
"this identity can do these actions on these resources." IAM policies follow the identity.

An S3 bucket policy is attached to the bucket itself. It says "these principals can do
these actions on this bucket." Bucket policies are evaluated regardless of what identity
is making the request.

The critical difference for this project: bucket policies can contain explicit DENY
statements that override IAM ALLOW statements. The `EnforceKMSEncryption` bucket policy
denies any `PutObject` request that does not include the KMS encryption header —
regardless of who is making the request, regardless of what their IAM policy says. Not
even an admin role can bypass an explicit DENY in a bucket policy without modifying
the bucket policy itself. This is why all `aws s3 cp` commands in this project include
`--sse aws:kms`, and why the smoke test's `put_object` call includes
`ServerSideEncryption='aws:kms'`.

---

**Q: What is KMS and why use it? What is an S3 Bucket Key?**

KMS is the AWS Key Management Service. It manages encryption keys. When you encrypt an
S3 object with KMS, S3 calls the KMS API to get a data key, uses that key to encrypt the
object, and stores the encrypted key alongside the object. To read the object later, S3
calls KMS again to decrypt the data key, then uses that to decrypt the object.

The reason to use KMS over S3's default AES-256 encryption: KMS gives you key policies
(you can control exactly which IAM principals can use the key for encryption vs decryption),
automatic key rotation, and a detailed audit trail in CloudTrail showing every time the
key was used and by whom. For a production system handling business transaction data,
this auditability matters.

The problem with KMS on S3 is that every single S3 PUT and GET generates a KMS API call.
At scale, these KMS API calls become expensive. An S3 Bucket Key solves this: instead of
calling KMS for every object, S3 generates one bucket-level data key and caches it.
Individual objects are encrypted with that cached key, reducing KMS API calls by
approximately 99%. This was the main cost objection to CMKs, and enabling Bucket Keys
removes it.

In dev, AWS-managed keys are used (no fixed monthly cost). In prod, customer-managed keys
with Bucket Keys enabled.

---

**Q: What is the OIDC setup for GitHub Actions? Why not just put AWS keys in GitHub
secrets?**

Static AWS credentials — `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` — are long-
lived. If they are ever exposed (leaked in a log, compromised in a secret management
system, stolen from a developer's machine), an attacker has persistent access to the AWS
account until the keys are rotated. Rotating them requires updating every system that
uses them.

OIDC (OpenID Connect) eliminates long-lived credentials. Instead of storing a key,
GitHub generates a short-lived JWT token when the workflow runs. The token identifies
the specific repository, branch, and workflow that is running. GitHub Actions sends this
token to AWS STS (Security Token Service), which verifies the token's signature against
GitHub's public JWKS endpoint. If the token is valid and the claims match the trust policy,
STS issues temporary credentials that last only for the duration of the workflow.

The trust policy in this project uses `StringEquals` on the `sub` claim set to exactly
`repo:eli-bigman/project-2-lakehouse:ref:refs/heads/main`. This means:

- Only the specific repository `eli-bigman/project-2-lakehouse` can assume the role
- Only workflows triggered by a push to `main` can assume it — not pull requests, not
  other branches, not any fork

If someone forks the repository and tries to run the deploy workflow, their token will
have a different `repo:` prefix in the sub claim, and STS will deny the assumption.

---

**Q: You have 2 Glue workers fixed. Why not 1, and why not auto-scaling?**

The AWS API minimum for a Glue Spark job is 2 workers. Setting `num_workers = 1` returns
an API error. This is a hard constraint, not a design choice.

Auto-scaling adds complexity for no benefit at this data volume. Glue auto-scaling has a
startup delay — it monitors job metrics and then provisions additional workers if needed.
For a job processing 2,768 rows, the job will complete before auto-scaling has time to
react. You would wait for the auto-scaling evaluation window and potentially get a longer
runtime rather than a shorter one.

Fixed allocation of 2 `G.1X` workers (4 vCPU, 16 GB RAM each) is more than sufficient
for the current volume. The allocation is predictable, the billing is predictable, and
there is no scaling-related latency.

---

## Section 3 — Data Engineering Concepts

**Q: What is schema enforcement and why does it matter?**

Schema enforcement means the system rejects data that does not match the expected
structure. If the Delta table for `dim_products` has four columns — `product_id`,
`department_id`, `department`, `product_name` — and a new file arrives with a fifth
column, the write fails rather than silently adding a column to the table.

Why this matters: in a production data warehouse, schema changes are breaking changes.
Analytics queries, dashboards, and downstream models are written against a known schema.
If a new column appears silently, nothing breaks immediately, but the next time a data
engineer runs `SELECT * FROM dim_products`, they get an unexpected column. More
dangerously, if a column name changes or a type changes (string to integer), downstream
queries silently start returning wrong results or fail in confusing ways.

Schema enforcement makes schema changes explicit and visible. When a schema change is
needed, it goes through code review as a migration — the schema file in `schemas.py` is
updated, a Delta migration is written, and the change is deployed deliberately.

---

**Q: What is Z-Ordering and when would you switch to physical partitioning?**

Z-Ordering is a Delta Lake file organisation technique. It co-locates rows with similar
values in the same Parquet files. After Z-Ordering by `order_date`, rows with the same
or nearby dates end up in the same files. The Delta transaction log records the min and
max `order_date` value in each file. When Athena or Spark runs a query with a date filter,
it reads the min/max statistics and skips entire files that cannot possibly contain
matching rows. This is called data skipping.

The benefit over physical partitioning at current volume: physical date partitioning would
create directories like `/order_date=2025-04-01/`, `/order_date=2025-04-02/`, etc. At
500 orders in a monthly file, if all orders share the same date, that is 12 partitions
per year each containing one month's worth of tiny files. The overhead of S3 list
operations and Delta log management for these tiny partitions exceeds the benefit.

The switch point: when a single date's data would hold approximately 1 GB. At that volume,
the pruning benefit of physical partitioning outweighs the overhead. The transition
requires a one-time table rewrite — `CONVERT TO DELTA` with a partition column, or
rebuilding with `INSERT OVERWRITE PARTITION`. This is documented and planned, just not
triggered yet.

---

**Q: What is a MERGE statement and how does it work in Delta?**

MERGE (sometimes called upsert) is a SQL operation that combines INSERT and UPDATE. It
compares incoming data against existing data in the target table on a match condition. For
each incoming row:

- If a matching row exists in the target → UPDATE the existing row
- If no matching row exists → INSERT the new row

In this project, the match condition is the natural key. For orders, it is `order_id`.
For order items, it is the composite of `order_id` and `product_id`.

```sql
MERGE INTO dwh.fct_orders AS target
USING incoming AS source
ON target.order_id = source.order_id
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *
```

Delta's implementation of MERGE is ACID — the entire operation is one transaction. Readers
never see a partially-merged table. If the Spark job crashes mid-MERGE, the transaction
log does not record the commit, so the table remains in its pre-MERGE state.

---

**Q: What is the _record_hash column for?**

Before writing to Delta, each incoming row gets a SHA-256 hash computed over all its
business columns. This hash is stored as `_record_hash`.

Its primary use is within-batch deduplication. If the same row appears twice in the same
incoming file (which happens in real-world data — duplicate rows from upstream systems),
both rows produce the same hash. The Spark job groups by natural key and hash, picks one
row per key, and drops duplicates before running the MERGE.

It also has a secondary use: detecting whether an incoming row is actually different from
what is already in the table. You can add a MERGE condition that only updates an existing
row if the new `_record_hash` differs from the stored one. If the data has not changed,
no write is needed. At large scale, this reduces the number of Parquet files written and
keeps the table compact. This optimisation is called a "no-op MERGE" guard.

---

**Q: What is the difference between `order_date` and `order_timestamp` in the schema?**

`order_timestamp` is the precise moment the order was placed — a full datetime with
hours, minutes, and seconds. `order_date` is derived from `order_timestamp` by truncating
to the calendar date. It is a computed column added during the Glue transformation.

`order_date` exists because analytics queries almost always filter by date, not by full
timestamp. "Show me all orders from April 1st" is a date comparison, not a timestamp
comparison. Having a pre-computed date column makes those queries faster and simpler.
It is also the Z-Ordering key — co-locating rows by date is more useful than co-locating
them by full timestamp.

---

## Section 4 — Terraform

**Q: What is Terraform state and why does it need to be stored remotely?**

Terraform state is the record of everything Terraform has created. It maps each resource
in your configuration to the real AWS resource it corresponds to — including the AWS-
generated IDs and ARNs that you cannot know until after the resource is created. The state
is how Terraform calculates what needs to change on the next apply.

If state is stored only on one person's laptop, no one else can safely run Terraform
against the same infrastructure. Two engineers running apply simultaneously with separate
local state files would each think they own the resources and create duplicates or
overwrite each other's work.

Remote state — stored in S3 — means there is one authoritative record. Everyone reads
from and writes to the same state. Combined with DynamoDB state locking, which prevents
two applies from running simultaneously, this makes team collaboration and CI/CD automation
safe.

---

**Q: What is `prevent_destroy` and why is it set on only some buckets?**

`prevent_destroy = true` in a Terraform lifecycle block tells Terraform to refuse to
destroy that resource. If `terraform destroy` is run and that resource is in the plan to
be destroyed, Terraform exits with an error before touching anything.

It is set on the four stateful buckets — raw, DWH, archive, quarantine — because those
hold the actual data. Losing them means losing the raw files, the Delta tables, and the
archived originals. That is unrecoverable without a backup.

It is not set on the stateless buckets — staging, athena-results, artifacts — because
those are safe to recreate. Staging files expire in 7 days anyway. Athena results can be
re-run. Artifacts are regenerated by CI/CD on the next deploy.

The teardown procedure overrides `prevent_destroy` by passing `-var="protect_stateful=false"`,
which removes the lifecycle block through the variable, and then setting `force_destroy=true`
on the bucket resource so Terraform can empty it before deletion.

---

**Q: What is a Terraform module and why use them?**

A module is a directory of Terraform files grouped together to serve a single concern.
You call a module from a parent configuration, passing in variables, and it creates
resources and exposes outputs.

This project has seven modules: s3_zones, dynamodb, iam, glue, lambda, stepfunctions,
and observability. Each module has one responsibility and a clear interface — variables
in, resources created, outputs out.

The alternative is putting everything in one large `main.tf` file. That would work for
a small project, but would become impossible to navigate and reason about as the
infrastructure grows. More importantly, modules can be reused across environments. A
`prod` environment would call the same seven modules with different variable values — no
code duplication.

---

**Q: What happens if you rename a key in a `for_each` map?**

Terraform destroys the resource with the old key and creates a new resource with the new
key. From Terraform's perspective, the old resource no longer exists in the configuration
(the key is gone), so it must be deleted. The new key is new, so it must be created.

For an S3 bucket, this means Terraform would plan to delete the bucket and all its
contents, then create a new empty bucket with the new name. For a bucket holding
production data, this is catastrophic and unrecoverable.

This is why the stateful S3 buckets in this project are declared as individual resource
blocks (`resource "aws_s3_bucket" "raw" {}`) rather than using `for_each`. Individual
resource blocks have stable addresses — `aws_s3_bucket.raw` — that do not depend on a
map key.

---

**Q: What is the `.terraform.lock.hcl` file and should it be committed?**

Yes, it should always be committed. It records the exact provider versions that were
selected when `terraform init` ran — including cryptographic hashes of the provider
binaries. When anyone else runs `terraform init` — whether on a different machine or in
CI — they get exactly the same provider version that was originally selected.

Without the lock file, `terraform init` would re-resolve provider versions against the
constraints in `required_providers`. The constraints say `>= 5.0` for the AWS provider.
Today that resolves to 6.50.0. In three months, it might resolve to 6.55.0 with a
breaking change. The lock file pins this to 6.50.0 until you explicitly upgrade by
running `terraform init -upgrade`.

---

**Q: How does CI/CD run Terraform without having AWS credentials stored as secrets?**

GitHub OIDC. When the deploy workflow runs, it calls `aws-actions/configure-aws-credentials`
with the IAM role ARN stored in the `AWS_ROLE_ARN` GitHub secret. That action exchanges
GitHub's OIDC token for temporary AWS credentials by calling STS
`AssumeRoleWithWebIdentity`. The resulting `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
and `AWS_SESSION_TOKEN` are injected as environment variables for the rest of the workflow.

By the time `terraform apply` runs, those environment variables are already set. The
Terraform AWS provider reads them automatically. This is why the `profile` attribute was
removed from `providers.tf` — on a CI runner, there is no local AWS profile file. The
provider only needs the environment variables to be present.

---

**Q: What does `TF_VAR_` do?**

Any environment variable prefixed with `TF_VAR_` is automatically read by Terraform as
the value for the corresponding variable. `TF_VAR_account_id=970547336735` is equivalent
to passing `-var="account_id=970547336735"` on the command line or setting it in a
`.tfvars` file.

In CI/CD, secrets that cannot be committed to the repository (account ID, email address)
are stored as GitHub repository secrets and injected into the workflow as `TF_VAR_*`
environment variables. The workflow never touches a `.tfvars` file for these values.

---

## Section 5 — CI/CD and GitHub Actions

**Q: Walk me through the CI pipeline — what checks run and in what order?**

Five stages run sequentially, each one dependent on the previous:

1. **Lint**: black checks formatting (would reformat fail), isort checks import ordering,
   flake8 checks code style. If any file is not formatted correctly, the pipeline fails
   here and nothing else runs.

2. **Security**: detect-secrets scans for accidentally committed secrets (API keys,
   passwords, tokens) by comparing against the committed `.secrets.baseline`. New secrets
   found that are not in the baseline fail the pipeline.

3. **Unit tests**: pytest runs `tests/unit/` — the Spark library tests that do not
   require AWS credentials. They use a local SparkSession.

4. **Integration tests**: pytest runs `tests/integration/` — end-to-end job tests against
   a local Delta warehouse. These take longer but validate the full Spark job behaviour.

5. **Terraform validate**: runs `terraform validate` in `infra/envs/dev/` to catch HCL
   syntax errors and provider schema violations without making any AWS API calls.

---

**Q: Why does the CI pipeline fail if black has not been run?**

black is opinionated about code formatting — spacing, line length, quote style. Running
`black --check` does not modify files; it returns a non-zero exit code if any file would
be reformatted. This means if a developer commits code without running black first, CI
fails.

The purpose is consistency. In a team environment, if everyone formats their code the same
way, diff views show only meaningful changes — no noise from spacing preferences. Reviews
focus on logic, not formatting.

The fix is always to run `python -m black src/ tests/` locally before committing. This
happened early in the project — 19 files had never been formatted and CI was failing on
every push until black was run once across the codebase.

---

**Q: What does `detect-secrets` do and why is there a baseline file?**

detect-secrets is a tool that scans source code for patterns that look like secrets —
AWS access keys, API tokens, passwords, private keys. Without a baseline, running
`detect-secrets scan` would report everything that looks like a secret in the codebase,
including documented examples, placeholder values, and test fixtures.

The baseline file (`.secrets.baseline`) is a snapshot of all the "secrets" that were
found during the initial scan and reviewed as safe — documented examples, not real
credentials. On subsequent runs, `detect-secrets scan --baseline .secrets.baseline` only
reports findings that are NOT in the baseline. If a real secret is accidentally committed,
it will not be in the baseline and will fail CI.

The baseline file must be committed to the repository. This project had an issue where it
was not committed, causing CI to fail with "invalid path" when it tried to use a baseline
that did not exist. The fix was to run `python -m detect_secrets scan > .secrets.baseline`
and commit the result.

---

**Q: The deploy workflow runs `terraform apply -auto-approve` directly. Is that a concern?**

It is a valid concern and the answer depends on the maturity of the project. In a mature
production setup, you would want to separate `terraform plan` (run on a pull request, for
review) from `terraform apply` (run after merge). This gives engineers visibility into
what infrastructure changes are coming before they land on main.

In this project, the deploy runs apply directly on merge to main. The compensating
controls are:

- Terraform changes must go through a pull request to get to main (branch protection)
- The plan is visible in CI on the PR
- The `prevent_destroy` lifecycle blocks prevent accidental destruction of stateful resources
- The `.terraform.lock.hcl` file ensures the same provider version runs in CI as locally

For a production system with multiple engineers and critical infrastructure, adding a
plan-on-PR step and requiring a human approval before apply would be the correct
improvement.

---

## Section 6 — Operational Questions

**Q: A pipeline run failed. How would you diagnose what went wrong?**

First, check the Step Functions execution in the AWS console or via the CLI. The
execution history shows exactly which state failed, the input it received, and the error
message. Step Functions captures the full input and output of every state transition, so
you can see what data was passed to each Lambda and Glue job.

If the failure is in a Glue job, the detailed logs are in CloudWatch under
`/aws-glue/jobs/output` and `/aws-glue/jobs/error`. The Glue job run ID is in the Step
Functions output and also in the DynamoDB ledger.

The DynamoDB ledger is the operational source of truth. The ledger row for the failed
batch will show its current status (FAILED or whatever stage it reached), which tells you
how far through the pipeline it got.

If the SNS alert email was received, that confirms the failure was captured by the
EventBridge rule watching for Glue job state changes.

Common failure patterns: a file has an unexpected schema (the normalizer will fail before
Glue even runs), a network timeout on a large file (check Glue job timeout configuration),
a DynamoDB conditional write failing because the batch was already claimed by a concurrent
execution.

---

**Q: How would you re-run a failed batch?**

If the batch failed before the DynamoDB ledger was updated to LOADED, the simplest
approach is to update the ledger record for that batch to a state that allows re-claiming
— or delete the ledger record entirely. Then re-trigger the Step Functions execution with
the same input.

If the batch failed mid-MERGE (Glue crashed while writing to Delta), Delta's transaction
log guarantees the table is in its pre-MERGE state. The failed transaction was never
committed. You can re-run the Glue job safely — the MERGE will produce the same result.

If the batch completed (reached LOADED) but you need to re-process it for a data
correction, you need to deliberately override the ledger. This is a manual intervention —
there is no automated re-processing of already-loaded batches without operator action.
This is intentional: idempotency protection should require explicit override.

---

**Q: How would you add a fourth dataset to this pipeline?**

The pipeline is parameterised, not hardcoded to three datasets. To add a fourth:

1. Define the schema in `src/lakehouse/schemas.py` — the column names, Spark types, and
   validation rules for the new dataset.

2. Register the Delta table in the Glue Data Catalog — add the DDL for the new table in
   the Glue module's Terraform.

3. Add the dataset to the Step Functions state machine — a new branch that runs the same
   normalise → Glue ingest sequence for the new dataset key.

4. Update the IAM policy for the Glue role if the new dataset writes to a new path.

5. Write unit and integration tests for the new schema and validation rules.

The Glue job itself does not change — it is parameterized on `dataset` and loads the
corresponding schema and validation rules dynamically from `schemas.py`.

---

**Q: The sandbox account restricts you to eu-west-1. How did that affect the project?**

The original design specified us-east-1, which is the most common default region. When
the first Terraform apply ran, every `s3:CreateBucket` call failed with AccessDenied.
The sandbox account has a managed policy (`DCEPrincipalDefaultPolicy-dce`) that denies
all actions outside eu-central-1 and eu-west-1.

This required changing:
- The provider `region` in `providers.tf`
- The backend `region` in `backend.tf`
- The state bucket name (it embeds the account ID, not the region, so the name did not
  change, but the region it was created in did)
- All hardcoded `us-east-1` ARN strings in IAM policy documents and CloudWatch
  configurations — these were replaced with `data.aws_region.current.name` to make all
  modules region-agnostic

The lesson: never hardcode a region string in IAM policies or resource ARNs. Use
`data "aws_region" "current" {}` and reference `data.aws_region.current.name`. This
makes every module portable across regions with no changes.

---

**Q: What would change in this architecture if monthly volume grew to 1 TB per file?**

Several things:

**Partitioning**: At 1 TB per monthly file, physical `order_date` partitioning becomes
worthwhile. Each day's data would hold meaningful volume (roughly 33 GB at constant daily
cadence), and Athena query pruning via partition elimination would be significantly faster
than data skipping alone.

**Glue workers**: 2 `G.1X` workers are fine for kilobytes. For a 1 TB file, you would
increase to `G.2X` or `G.4X` workers and likely 10-20 workers with auto-scaling enabled.
At that scale, the auto-scaling decision delay (a few minutes) is negligible compared to
the total job runtime.

**Lambda for normalization**: Lambda has a 15-minute timeout and a 10 GB memory limit.
A 1 TB Excel file would exceed both. At that scale, the normalizer would need to move to
a Glue Python-shell job or Glue Spark job itself. The architecture would look like: detect
format → if small, Lambda normalize → if large, Glue normalize → Glue ingest.

**Delta OPTIMIZE**: The MERGE operation generates many small files. At high volume, the
optimize job (which compacts small files into larger ones) becomes more important and
would need to run more frequently — possibly after every load rather than on a schedule.

**Athena costs**: Athena charges per TB scanned. With partitioning and Z-Ordering in
place, the scanned volume per query stays manageable. Without them, a 1 TB table
scanned fully per query at $5/TB would become expensive quickly.

---

**Q: What is the cost of running this infrastructure for one month without any pipeline
runs?**

The standing costs (resources that exist regardless of usage):

- 7 S3 buckets: effectively zero if empty (S3 Standard charges per GB stored, not per
  bucket)
- 2 DynamoDB tables: PAY_PER_REQUEST billing means zero cost if no reads or writes occur
- Lambda functions: zero cost if never invoked
- Glue job: zero cost if never run
- Step Functions: zero cost if no executions
- CloudWatch log groups: zero cost if no logs ingested
- SNS topic: zero cost if no messages
- KMS: in dev, AWS-managed keys are free. CMKs in prod cost ~$1/key/month.
- Athena: zero cost if no queries run

The only fixed cost in dev is the S3 storage for the state bucket and the Terraform lock
table (minimal). The total standing cost of the dev environment with no activity is under
$1 per month.

A full deploy-test-destroy cycle (upload data, run pipeline, query Athena, destroy) costs
under $1.

---

**Q: What would you improve if you were taking this to production?**

Several things are deliberately simplified for the dev/sandbox phase:

**IAM**: The GHA deploy role currently has `AdministratorAccess`. In production, this
should be scoped down to exactly the permissions needed to create and manage the specific
resources in the Terraform configuration. Least privilege applies to the deployer role
too.

**CMKs for all environments**: Dev uses AWS-managed keys to avoid fixed costs. Production
needs CMKs for audit trails and key policy control.

**Plan before apply in CI**: The deploy workflow currently runs apply directly. A
production workflow would run plan on the PR (so engineers review infrastructure changes
before merge), and apply only after merge.

**Glue job concurrency limits**: Two concurrent Glue jobs for the same dataset could run
if Step Functions is triggered twice quickly. The DynamoDB ledger prevents data
duplication, but the compute cost doubles. A Glue job concurrency limit (max 1 concurrent
run per job) would prevent wasted compute.

**Monitoring**: The CloudWatch dashboard and alarms are basic. A production system would
track pipeline latency (time from file landing to Athena-queryable), data quality metrics
(reject rate per dataset over time), and cost per run.

**Testing environment parity**: The integration tests use a local SparkSession with Delta.
A staging environment (a second Terraform workspace with real AWS resources) would catch
issues that only manifest with real Glue or real S3 — network partitions, IAM policy
gaps, eventual consistency in S3 listing.
