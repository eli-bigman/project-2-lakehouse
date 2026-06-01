# Reference Hub — "When Stuck, Look Here"

> **Purpose.** A single jump-off point for any agent (human or AI) executing this project.
> When you hit a wall, start here: find the symptom in §4, follow it to the owning
> internal doc, then to the authoritative external source. **Internal docs are the source
> of truth for *our* decisions; external links are the source of truth for *how the tech
> works*.** If the two ever conflict, the internal doc + `decision.md` explains why we
> deviated — read the rationale before "fixing" it.

---

## 1. How to use this file
1. **Identify the layer** you're stuck in (ingestion? Delta MERGE? Step Functions?
   Terraform? CI/CD?).
2. **Go to the owning internal doc** (§2) — it has the design + acceptance criteria.
3. **Open the external reference** (§3) for API/syntax details.
4. If it's an error/symptom, use the **troubleshooting playbook** (§4) to route fast.
5. Honor the **Design Contract** (`architecture.md` §3) — never re-decide what it fixes.

## 2. Internal documents (our source of truth)
| Topic | Doc | Read when |
|-------|-----|-----------|
| Strategy, objectives, traceability | `master_plan.md` | starting / scoping |
| **Design Contract (zones, schemas, keys)** | `architecture.md` §3 | always, before any change |
| Why a decision was made + **review dispositions** (`.ai/review.md`) | `decision.md` | tempted to deviate / reconciling a review comment |
| Repo layout | `directory_structure.md` | placing a new file |
| Ingestion, xlsx→Parquet, backfill | `data_handling.md` | source/landing/archive issues |
| Validation rules, quarantine | `data_validation.md` | a record is rejected/should be |
| Cleaning, dedup, MERGE | `transformation_logic.md` | upsert / dup problems |
| Delta tables, partition, OPTIMIZE/VACUUM | `delta_lake_design.md` | table layout / small files |
| Glue job config & params | `glue_jobs.md` | job won't run / misconfigured |
| DynamoDB ledger/watermark | `dynamodb_schema.md` | idempotency / state |
| Step Functions state machine | `orchestration_stepfunctions.md` | flow / branching / trigger |
| Retries, alerts, DLQ | `error_handling.md` | something failed |
| Glue Catalog + Athena | `catalog_and_athena.md` | query / catalog issues |
| Terraform / IaC | `terraform.md` | provisioning / drift |
| GitHub Actions CI/CD | `cicd_github_actions.md` | pipeline / deploy |
| Tests + fixtures | `testing_strategy.md` | writing/failing tests |
| IAM, KMS, secrets | `security_iam.md` | access denied / encryption |
| Logs, metrics, alarms | `monitoring_observability.md` | observability / SLA |
| Sprints + agent roles | `sprint_planning.md` | sequencing work |
| Go-live + rollback | `production_deployment.md` | deploying / recovering |

## 3. External references (authoritative docs)

> Links are canonical entry points on official domains. Versions: Glue 4.0+, Spark 3.3+,
> Athena engine v3, Terraform ≥1.6 (see `decision.md` / `glue_jobs.md` for pins).

### Delta Lake & Spark (transform / MERGE / OPTIMIZE)
- Delta Lake docs: https://docs.delta.io/latest/index.html
- Delta `MERGE` / upsert: https://docs.delta.io/latest/delta-update.html#upsert-into-a-table-using-merge
- Delta `OPTIMIZE` / Z-Order: https://docs.delta.io/latest/optimizations-oss.html
- Delta `VACUUM` & retention: https://docs.delta.io/latest/delta-utility.html#vacuum
- Schema enforcement & evolution: https://docs.delta.io/latest/delta-batch.html#schema-validation
- Time travel / RESTORE (rollback): https://docs.delta.io/latest/delta-batch.html#query-an-older-snapshot-of-a-table-time-travel
- PySpark SQL API: https://spark.apache.org/docs/latest/api/python/reference/index.html

### AWS Glue (ETL compute)
- Glue developer guide: https://docs.aws.amazon.com/glue/latest/dg/what-is-glue.html
- Glue worker types and billing specifications: https://docs.aws.amazon.com/glue/latest/dg/aws-glue-api-jobs-job.html#resources-aws-glue-api-jobs-job-WorkerType
- Glue + Delta Lake (`--datalake-formats`): https://docs.aws.amazon.com/glue/latest/dg/aws-glue-programming-etl-format-delta-lake.html
- Glue job parameters: https://docs.aws.amazon.com/glue/latest/dg/aws-glue-programming-etl-glue-arguments.html
- AWS Lambda (normalizer runtime — ADR-011): https://docs.aws.amazon.com/lambda/latest/dg/welcome.html
- Lambda container images / layers (pandas/openpyxl): https://docs.aws.amazon.com/lambda/latest/dg/python-image.html
- Glue Python-shell jobs (normalizer *fallback* for oversized files): https://docs.aws.amazon.com/glue/latest/dg/add-job-python.html
- Glue Data Catalog: https://docs.aws.amazon.com/glue/latest/dg/catalog-and-crawler.html

### AWS Step Functions (orchestration)
- Developer guide: https://docs.aws.amazon.com/step-functions/latest/dg/welcome.html
- Amazon States Language (ASL): https://docs.aws.amazon.com/step-functions/latest/dg/concepts-amazon-states-language.html
- Error handling (Retry/Catch): https://docs.aws.amazon.com/step-functions/latest/dg/concepts-error-handling.html
- `Map` state (backfill fan-out): https://docs.aws.amazon.com/step-functions/latest/dg/amazon-states-language-map-state.html
- Service integration with Glue: https://docs.aws.amazon.com/step-functions/latest/dg/connect-glue.html

### Amazon Athena (query)
- User guide: https://docs.aws.amazon.com/athena/latest/ug/what-is.html
- Querying Delta Lake: https://docs.aws.amazon.com/athena/latest/ug/delta-lake-tables.html
- Workgroups & cost control: https://docs.aws.amazon.com/athena/latest/ug/workgroups.html
- Partition projection (only if promoted to physical partitioning — ADR-005): https://docs.aws.amazon.com/athena/latest/ug/partition-projection.html

### Amazon S3 & DynamoDB (storage / control plane)
- S3 lifecycle: https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lifecycle-mgmt.html
- S3 Intelligent-Tiering (ADR-016, vs Glacier for small files): https://docs.aws.amazon.com/AmazonS3/latest/userguide/intelligent-tiering.html
- S3 Bucket Keys (ADR-017, cut KMS API cost): https://docs.aws.amazon.com/AmazonS3/latest/userguide/bucket-key.html
- S3 EventBridge notifications (trigger): https://docs.aws.amazon.com/AmazonS3/latest/userguide/EventBridge.html
- DynamoDB conditional writes (idempotency): https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/WorkingWithItems.html#WorkingWithItems.ConditionalUpdate
- DynamoDB PITR: https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/PointInTimeRecovery.html

### Terraform / IaC
- AWS provider: https://registry.terraform.io/providers/hashicorp/aws/latest/docs
- S3 backend (remote state + lock): https://developer.hashicorp.com/terraform/language/settings/backends/s3
- `lifecycle` (prevent_destroy / replace): https://developer.hashicorp.com/terraform/language/meta-arguments/lifecycle
- tfsec: https://aquasecurity.github.io/tfsec/ · checkov: https://www.checkov.io/ · tflint: https://github.com/terraform-linters/tflint

### GitHub Actions / CI/CD & security
- GitHub Actions docs: https://docs.github.com/actions
- OIDC to AWS (no static keys): https://docs.github.com/actions/deployment/security-hardening-your-deployments/configuring-openid-connect-in-amazon-web-services
- Environments + required reviewers: https://docs.github.com/actions/deployment/targeting-different-environments/using-environments-for-deployment
- AWS IAM best practices: https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html
- AWS KMS: https://docs.aws.amazon.com/kms/latest/developerguide/overview.html

### Python tooling (normalization + tests)
- pandas `read_excel`: https://pandas.pydata.org/docs/reference/api/pandas.read_excel.html
- openpyxl (xlsx engine): https://openpyxl.readthedocs.io/
- delta-rs (Python deltalake): https://delta-io.github.io/delta-rs/
- AWS SDK for Pandas (awswrangler): https://aws-sdk-pandas.github.io/
- pytest: https://docs.pytest.org/ · chispa (Spark test asserts): https://github.com/MrPowers/chispa
- moto (mock AWS, e.g. DynamoDB): https://docs.getmoto.org/

## 4. Troubleshooting playbook (symptom → route)
| Symptom | First look | Then |
|---------|-----------|------|
| Spark can't read the source file | `data_handling.md` §3 (it's `.xlsx`; normalize first) | pandas/openpyxl links |
| Duplicate rows appear in a table | `transformation_logic.md` §3–4 (dedup + MERGE key) | Delta MERGE link |
| Re-running a file double-loads | `dynamodb_schema.md` §2.1 (conditional write) + ADR-007 | DynamoDB conditional writes |
| Records unexpectedly rejected | `data_validation.md` §3 (rule catalog) | — |
| Run failed at QualityGate | reject_rate > 5% — `data_validation.md` §6, `error_handling.md` | — |
| Glue job won't start / wrong config | `glue_jobs.md` §2–3 | Glue job params link |
| Athena can't read Delta (native v3) | `catalog_and_athena.md` §2–3 (`table_type=DELTA`; no manifest/`MSCK`) | Athena Delta link |
| State machine stuck / not triggering | `orchestration_stepfunctions.md` §2, §5 | ASL + Step Functions error handling |
| `terraform plan` shows surprise replacement | `terraform.md` §5 (identity churn) | `lifecycle` meta-arg |
| CI deploy can't auth to AWS | `cicd_github_actions.md` §5, `security_iam.md` | GitHub OIDC→AWS |
| "Access Denied" at runtime | `security_iam.md` §2 (least-privilege roles) | IAM best practices |
| Bad batch landed in prod | `production_deployment.md` §4 (Delta time travel RESTORE) | Delta time travel |
| Need to backfill history | `data_handling.md` §4.2, `orchestration_stepfunctions.md` §6 | Step Functions `Map` |

## 5. Skills available in this environment (for the executing agent)
- **`terrashark`** — Terraform/OpenTofu failure-mode diagnosis (identity churn, secret
  exposure, blast radius, CI drift, compliance gates). Use when writing/reviewing IaC;
  the five failure modes are baked into `terraform.md` §5.
- **`code-review` / `security-review`** — review diffs before merge to `main`
  (`cicd_github_actions.md`).
- **`deep-research`** — for open questions in `decision.md` needing external investigation.

## 6. Maintenance of this file
This is a living index. When you add a new component or external dependency, add its
authoritative link here and a row to §4. Keep links to **official** docs only; pin
versions where behavior is version-sensitive.
