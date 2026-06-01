# Security, IAM & Data Protection

> Cites Design Contract + `terraform.md`, `cicd_github_actions.md`. Cross-cutting NFR.

## 1. Principles
- **Least privilege** — each component role can touch only the zones/tables it needs.
- **No long-lived credentials** — OIDC for CI; IAM roles for runtime (ADR-013).
- **Encryption everywhere** — at rest (KMS) + in transit (TLS).
- **Blast-radius isolation** — per-zone, per-env boundaries (terrashark guard).

## 2. Runtime roles (least privilege)

| role | may | may not |
|------|-----|---------|
| `normalize-lambda-role` | read `raw`, write `staging`, put ledger item | touch `dwh`/`archive` |
| `glue-ingest-role` | read `staging`, read/write `dwh`, write `quarantine`, read/write ledger+watermark | delete `raw`, write `artifacts` |
| `archive-lambda-role` | read `raw`, write `archive`, delete/tag `raw`, update ledger | write `dwh` |
| `stepfunctions-role` | `glue:StartJobRun`, `lambda:Invoke`, `athena:StartQueryExecution`, `sns:Publish`, `states:*` (own SM) | direct S3 data writes |
| `athena-role` | query catalog tables, write results bucket | write `dwh` |
| `gha-deploy-role` (OIDC) | terraform-managed resources, upload artifacts | runtime data access |

Each policy is resource-scoped to the env-suffixed ARNs (no `*` on buckets/tables).
Policy simulation (`iam simulate-principal-policy`) is part of Terraform verification
(`terraform.md` §2).

## 3. Encryption & key management (ADR-017, review 2.2)
- **prod: KMS CMKs** per data class (S3 data, DynamoDB, CloudWatch Logs); key policies
  grant only project roles (rotation + audit + least-privilege at the key). **dev:**
  AWS-managed keys (`aws/s3`, `aws/dynamodb`) are acceptable to avoid fixed per-key fees.
- S3: SSE-KMS default encryption **with `bucket_key_enabled = true`** (caches data keys →
  ~99% fewer KMS API requests, neutralizing the CMK cost objection) + **block public
  access** on all buckets + two mandatory `Deny` statements in every data bucket policy
  (ADR-021):
  1. **`EnforceHTTPS`** — deny any `s3:*` where `aws:SecureTransport = false` (block non-TLS).
  2. **`EnforceKMSEncryption`** — deny `s3:PutObject` where
     `s3:x-amz-server-side-encryption != "aws:kms"` (block SSE-S3 override).
  Together these prevent both unencrypted-in-transit and KMS-bypassed-at-rest scenarios.
  Default bucket encryption alone is insufficient: an upload explicitly specifying
  `AES256` overrides the bucket default and lands without KMS protection.
- DynamoDB: SSE-KMS + PITR (`dynamodb_schema.md`).
- Athena results + Glue temp: encrypted.

## 4. Secrets
- No static AWS keys (OIDC). Any third-party secret (e.g. Slack webhook for alerts) lives
  in **AWS Secrets Manager** / GitHub Encrypted Secrets, never in code or Terraform state.
- `detect-secrets` pre-commit + CI scan; sensitive TF outputs marked `sensitive`.

## 5. Network (optional hardening)
- Glue/Lambda in a VPC with **S3/DynamoDB/Athena VPC endpoints** so data never leaves the
  AWS network; security groups least-open. (Optional given serverless services; documented
  for a hardened prod.)

## 6. Data governance & PII
- `user_id` treated as a non-identifying surrogate (assumption — `decision.md` open Q3).
  If real PII ever arrives, add column-level controls via **Lake Formation** + tokenization
  before Silver.
- Quarantine retention 180d then expire; raw/archive in S3 Standard (no Glacier, ADR-016)
  — minimize retained sensitive data via lifecycle expiry, not cold-tiering.
- **Audit:** CloudTrail on Glue/S3/DynamoDB/Athena management + data events on sensitive
  buckets.

## 7. Compliance gates (terrashark "compliance gate")
CI enforces: bucket encryption + `bucket_key_enabled` + versioning + public-access-block
present, **`EnforceHTTPS` + `EnforceKMSEncryption` Deny statements present** on every data
bucket, no wildcard admin policies, no plaintext secrets, KMS on stateful stores — via
`tfsec`/`checkov`. (ADR-021)

## 8. Acceptance criteria
- Policy simulation proves a role cannot cross its zone boundary.
- All buckets: encrypted (SSE-KMS + Bucket Keys), versioned, public access blocked.
- Every data bucket policy contains both `EnforceHTTPS` and `EnforceKMSEncryption` Deny
  statements; uploading with `AES256` header returns `403`.
- No secret material in repo or state; CI scans pass.
- CloudTrail records pipeline data/management events.
