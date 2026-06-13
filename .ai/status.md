# .ai/status.md — Lakehouse Build Progress

> Updated by the execution agent after each sprint. Commit this file with each
> incremental push so the human operator can check progress at any time.

## Legend
| Symbol | Meaning |
|--------|---------|
| ✅ | Done — artifact committed |
| 🔄 | In progress |
| ⏳ | Pending — not yet started |
| ❌ | Blocked or failed |

---

## Session Info
| Key | Value |
|-----|-------|
| AWS Account | 647594457599 |
| AWS Profile | personal |
| AWS Region | us-east-1 |
| TF Env | dev |
| GitHub Repo | eli-bigman/ecom-lakehouse |
| Agent | claude-sonnet-4-6 |
| Session Start | 2026-06-10 |

---

## Sprint Status

### S0 — Foundations 🔄
| Task | Status | Notes |
|------|--------|-------|
| Repo scaffolded | 🔄 | Creating now |
| .gitignore / .env.example | 🔄 | |
| pyproject.toml / requirements.txt | 🔄 | |
| pre-commit config | 🔄 | |
| Makefile | 🔄 | |
| README.md | 🔄 | |
| .ai/status.md | ✅ | This file |

### S1 — Infrastructure (Terraform) ⏳
| Task | Status | Notes |
|------|--------|-------|
| Terraform state bootstrap (S3+DDB) | ⏳ | Manual via AWS CLI |
| infra/modules/s3_zones | ⏳ | 7 buckets, stateful explicit |
| infra/modules/kms | ⏳ | dev: AWS-managed; prod: CMK |
| infra/modules/dynamodb | ⏳ | ledger + watermarks |
| infra/modules/iam | ⏳ | per-role least privilege |
| infra/modules/glue | ⏳ | Glue DB + jobs |
| infra/modules/stepfunctions | ⏳ | |
| infra/modules/athena_catalog | ⏳ | |
| infra/modules/observability | ⏳ | SNS + CloudWatch |
| infra/envs/dev | ⏳ | |
| terraform apply (dev) | ⏳ | |

### S2 — Ingestion & Normalization ⏳
| Task | Status | Notes |
|------|--------|-------|
| src/normalize/normalize_to_parquet.py | ⏳ | Lambda handler |
| Lambda IAM role | ⏳ | via infra/modules/iam |
| Lambda Terraform resource | ⏳ | |
| DynamoDB ledger client (lakehouse.ledger) | ⏳ | |

### S3 — Transform, Validate & Delta MERGE ⏳
| Task | Status | Notes |
|------|--------|-------|
| src/lakehouse/config.py | ⏳ | zone names, table specs |
| src/lakehouse/schemas.py | ⏳ | StructType per dataset |
| src/lakehouse/io.py | ⏳ | read/write helpers |
| src/lakehouse/validation.py | ⏳ | rule engine |
| src/lakehouse/transforms.py | ⏳ | typing, dedup, audit cols |
| src/lakehouse/merge.py | ⏳ | Delta MERGE/upsert |
| src/lakehouse/ledger.py | ⏳ | DynamoDB client |
| src/lakehouse/logging_utils.py | ⏳ | structured logging |
| src/glue_jobs/ingest.py | ⏳ | parameterized entrypoint |
| src/glue_jobs/optimize.py | ⏳ | OPTIMIZE + VACUUM |
| tests/unit/ | ⏳ | pytest + chispa |
| tests/integration/ | ⏳ | local Delta |
| tests/data/ fixtures | ⏳ | golden + dirty |

### S4 — Orchestration ⏳
| Task | Status | Notes |
|------|--------|-------|
| orchestration/state_machine.asl.json | ⏳ | templated ASL |
| EventBridge rule | ⏳ | S3 → SF trigger |
| Lambda: claim, archive, validate-schema | ⏳ | |
| SF Terraform resource | ⏳ | |

### S5 — Catalog, CI/CD, Hardening, UI ⏳
| Task | Status | Notes |
|------|--------|-------|
| src/athena/validation_queries.sql | ⏳ | |
| .github/workflows/ci.yml | ⏳ | |
| .github/workflows/deploy.yml | ⏳ | |
| src/ui/ Streamlit app | ⏳ | |
| OIDC provider + GHA role | ⏳ | |

### S6 — Deploy & Teardown ⏳
| Task | Status | Notes |
|------|--------|-------|
| terraform apply (dev) | ⏳ | |
| Upload sample data to S3 | ⏳ | |
| Trigger Step Functions execution | ⏳ | |
| Athena smoke query | ⏳ | |
| terraform destroy | ⏳ | After validation |

---

## Cost Tracking
| Resource | Est. cost/hr | Notes |
|----------|-------------|-------|
| S3 (7 buckets, ~MB) | ~$0.00 | Negligible at dev volume |
| KMS CMK | $0 dev | AWS-managed in dev |
| DynamoDB (on-demand) | ~$0.00 | Tiny traffic |
| Glue jobs (2xG.1X) | ~$0.22/run | Per Glue DPU-min; 1 min min |
| Step Functions | ~$0.00 | <1000 transitions/month |
| Lambda (3 fns) | ~$0.00 | ms billing at tiny volume |
| Athena | ~$0.00 | <1 MB scanned |
| **Total (apply+smoke+destroy)** | **< $1.00** | Under $26 budget |

---

## Known Issues / Blockers
_None at session start._

---

## Decision Overrides & Corrections
| # | Original doc | Correction | Reason |
|---|-------------|------------|--------|
| 1 | terraform.md: `prevent_destroy=true` | Set `protect_stateful=false` variable for dev teardown | `prevent_destroy` would block `terraform destroy` in ephemeral validation run. We use a tfvar to toggle it off before destroy. |

---

## Validation Checklist (post-deploy, pre-destroy)
- [ ] `aws sts get-caller-identity --profile personal` shows expected account
- [ ] `terraform plan` shows No changes after apply
- [ ] All 7 S3 buckets exist with KMS encryption + Bucket Keys
- [ ] DynamoDB ledger + watermarks tables exist with PITR
- [ ] Step Functions execution on sample data reaches Succeed
- [ ] Athena query on `fct_orders` returns >0 rows
- [ ] `terraform destroy` completes cleanly
