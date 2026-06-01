# Infrastructure as Code — Terraform Plan

> Cites Design Contract (`architecture.md` §3) + `security_iam.md`. Owns all infra
> provisioning. ADR-012 (Terraform sole IaC), ADR-013 (OIDC).

## 0. Working style — "agent stick-holding" + the "telephone" guardrail (assumption)

> **Interpretation (stated assumption, per `decision.md` open-questions):** this plan is
> written in an **agent stick-holding** style — prescriptive, ordered, self-contained,
> and *verifiable* steps an execution agent (human or AI) can follow without inferring
> missing context. The **"telephone" guardrail** means: each step carries everything the
> next executor needs, so intent does not degrade as work is handed agent-to-agent (the
> telephone game). Every step below therefore has: **inputs → action → expected output →
> verification.** No step assumes knowledge held only in a previous agent's head.

The `terrashark` skill (available in this environment) targets exactly the IaC failure
modes we guard against in §5. We do **not** invoke it now (no code this phase); we fold
its five failure modes in as a review checklist for the implementation phase.

## 1. Tooling & layout
- Terraform (>= 1.6) with the AWS provider, pinned versions.
- **Remote state:** S3 backend + DynamoDB state-lock table, per env, encrypted.
- Module-per-concern under `infra/modules/`; env composition under `infra/envs/{dev,prod}`
  (see `directory_structure.md`).
- `dev` and `prod` share modules; differ only by `tfvars` → environment parity.

## 2. Modules (each = one ordered, verifiable unit)

| module | provisions | key inputs | verification |
|--------|-----------|------------|--------------|
| `s3_zones` | 7 buckets — **stateful** (`raw`,`dwh`,`archive`,`quarantine`) as explicit blocks w/ `prevent_destroy`; **stateless** (`staging`,`athena-results`,`artifacts`) via `for_each` — + lifecycle + versioning + SSE-KMS w/ **Bucket Keys** + **`EnforceHTTPS` + `EnforceKMSEncryption` Deny policies** (ADR-021) | env, prefix, kms_key | `get-bucket-encryption` shows KMS + `BucketKeyEnabled`; `get-bucket-policy` shows both Deny statements; no Glacier transition; lifecycle present |
| `dynamodb` | ledger + watermarks (+ GSI, PITR, SSE) — *schema_registry removed, review 1.5* | env | `describe-table` shows PITR on, on-demand billing |
| `iam` | per-role least-privilege policies (Glue/**Lambda**/SF/Athena) + OIDC deploy role | account, repo | `iam simulate-principal-policy` denies cross-zone writes |
| `glue` | Glue **Spark** jobs (ingest per dataset, optimize) — **`G.1X`, `NumberOfWorkers=2`, auto-scaling off** (ADR-020); DB, (optional) crawler; **normalizer is a Lambda** (see `lambda` resources) | script S3 paths | `get-job` shows `G.1X`, 2 workers, auto-scaling disabled, Delta args |
| `stepfunctions` | state machine from templated ASL + EventBridge rules | job ARNs, sns arn | `describe-state-machine`; test execution succeeds |
| `athena_catalog` | workgroup + result bucket binding + (views) | results bucket | `get-work-group` enforces result location |
| `observability` | SNS topic + CloudWatch alarms/dashboards + log groups | email/slack | alarm in `INSUFFICIENT_DATA`→`OK` after test |

## 3. Step-by-step provisioning order (stick-holding)
Dependencies dictate order; each step states input→action→output→verify.

1. **Bootstrap remote state** — *Input:* account, region. *Action:* create state bucket +
   lock table (one-time, via a `bootstrap` config). *Output:* `backend.tf` values.
   *Verify:* `terraform init` succeeds against the backend.
2. **KMS keys** — *Action:* in **prod** create CMKs for S3/DynamoDB/logs (in **dev**,
   AWS-managed keys are acceptable — ADR-017). *Verify:* key policy allows only the project
   roles.
3. **`s3_zones`** — *Verify:* all 7 buckets exist, versioned, encrypted with
   **`bucket_key_enabled = true`**, lifecycle set with **no Glacier transition** (ADR-016);
   stateful buckets carry `prevent_destroy`.
4. **`dynamodb`** — *Verify:* tables + GSI + PITR.
5. **`iam`** — *Verify:* policy simulation denies blast-radius actions (§5).
6. **`glue`** (after artifacts bucket exists + wheel uploaded by CI) — *Verify:* jobs
   created with correct Delta config.
7. **`athena_catalog`** — *Verify:* workgroup enforces results location.
8. **`observability`** — *Verify:* SNS subscription confirmed; test alarm fires.
9. **`stepfunctions`** (last — references all ARNs) — *Verify:* a dry-run execution on a
   sample key reaches `Succeed`.

## 4. State, environments, drift
- One state file per env (S3 key `env/{dev|prod}/terraform.tfstate`), DynamoDB lock.
- `terraform plan` in CI on PRs (no apply); `apply` only from `main` via OIDC (§CI/CD).
- **Drift detection:** scheduled `terraform plan` job alerts if real infra diverges from
  code (a `terrashark` "CI drift" guard).

## 5. terrashark failure-mode guardrails (review checklist for implementation)
1. **Identity churn** — avoid resources that force-replace on benign change (e.g. name
   changes that recreate IAM roles / buckets). Use stable names + `lifecycle` blocks;
   review every `plan` for unexpected `-/+` replacements before apply.
2. **Secret exposure** — **no secrets in HCL/state in plaintext.** Use OIDC (no static
   keys), KMS, and `detect-secrets`/`tfsec` in CI; mark sensitive outputs `sensitive = true`;
   never log state.
3. **Blast radius** — least-privilege IAM (a role can write only to its zone); separate
   state per env; `prevent_destroy` on stateful buckets/tables; require manual approval to
   apply in `prod`.
4. **CI drift** — scheduled drift `plan`; fail CI if plan is non-empty on a protected env
   without an associated PR.
5. **Compliance gates** — `tfsec`/`checkov` + `tflint` + `terraform validate` + `fmt`
   gates in CI; enforce encryption, versioning, public-access-block on every bucket.

## 6. Validated S3 security module sketch (ADR-017/ADR-018/ADR-021)
Stateful buckets are **explicit blocks** with `prevent_destroy`; `for_each` is reserved for
stateless zones. The bucket policy enforces both HTTPS and SSE-KMS at upload (ADR-021) —
the validated pattern from the round-2 review:

```hcl
# modules/s3_zones/stateful.tf  — one block per stateful zone (raw/dwh/archive/quarantine)

# KMS CMK (prod); aws-managed key reused in dev via tfvars
resource "aws_kms_key" "s3" {
  description             = "KMS key for ${var.prefix} S3 buckets (${var.env})"
  deletion_window_in_days = 7
  enable_key_rotation     = true
}

resource "aws_s3_bucket" "dwh" {
  bucket = "${var.prefix}-dwh-${var.env}"
  lifecycle { prevent_destroy = true }   # ADR-018: apply errors instead of destroying warehouse
}

resource "aws_s3_bucket_public_access_block" "dwh" {
  bucket                  = aws_s3_bucket.dwh.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "dwh" {
  bucket = aws_s3_bucket.dwh.id
  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.s3.arn
      sse_algorithm     = "aws:kms"
    }
    bucket_key_enabled = true   # ADR-017: ~99% fewer KMS API calls
  }
}

# ADR-021: enforce HTTPS in transit AND SSE-KMS at rest; default encryption alone is
# insufficient — a client explicitly setting AES256 would override it silently.
resource "aws_s3_bucket_policy" "dwh" {
  bucket = aws_s3_bucket.dwh.id
  policy = data.aws_iam_policy_document.dwh_policy.json
}

data "aws_iam_policy_document" "dwh_policy" {
  # Rule 1: deny non-HTTPS traffic
  statement {
    sid    = "EnforceHTTPS"
    effect = "Deny"
    principals { type = "*"; identifiers = ["*"] }
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.dwh.arn, "${aws_s3_bucket.dwh.arn}/*"]
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
  # Rule 2: deny uploads that specify anything other than aws:kms encryption
  statement {
    sid    = "EnforceKMSEncryption"
    effect = "Deny"
    principals { type = "*"; identifiers = ["*"] }
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.dwh.arn}/*"]
    condition {
      test     = "StringNotEquals"
      variable = "s3:x-amz-server-side-encryption"
      values   = ["aws:kms"]
    }
  }
}
# (raw/archive/quarantine mirror this pattern; no Glacier lifecycle transition — ADR-016)

# modules/s3_zones/stateless.tf  — for_each safe for stateless zones (destroy = fine)
resource "aws_s3_bucket" "stateless" {
  for_each = toset(["staging", "athena-results", "artifacts"])
  bucket   = "${var.prefix}-${each.key}-${var.env}"
}
# + versioning, public_access_block, SSE per stateless zone
```

## 7. Acceptance criteria
- `terraform plan` is clean (no drift) on a deployed env.
- No plaintext secrets in code or state; all stateful resources encrypted + versioned.
- Every data bucket: `EnforceHTTPS` + `EnforceKMSEncryption` Deny statements verified
  (`aws s3api get-bucket-policy`); uploading with `AES256` header returns `403`.
- IAM policy simulation proves zone isolation.
- Glue jobs report `G.1X`, 2 workers, auto-scaling disabled in `get-job`.
- A full apply produces a working pipeline reachable by a sample Step Functions run.
