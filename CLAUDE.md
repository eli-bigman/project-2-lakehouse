# CLAUDE.md — E-Commerce Lakehouse Architecture

## What this project is

Production-grade **medallion Lakehouse on AWS** for an e-commerce platform:
- Raw transactional files (S3) → normalize (Lambda) → validate + transform (Glue + Spark + Delta Lake) → curated DWH (S3, Delta) → queryable via Athena
- Orchestrated by **AWS Step Functions**, deployed via **GitHub Actions CI/CD** scoped to `main`
- **Current phase: Planning only.** All deliverables are `.md` files in `docs/`. No Terraform, no Glue scripts, no GitHub workflows exist yet.

---

## Read these first

| File | Why |
|------|-----|
| `docs/master_plan.md` | Entry point — objectives, user stories, document index |
| `docs/architecture.md` | **Design Contract (source of truth)** — zone names, schemas, keys, ADR references. Check here before any decision. |
| `docs/decision.md` | All ADRs with rationale + two rounds of review dispositions |
| `docs/reference.md` | Stuck? Start here — routes symptoms to owning doc + external links |
| `.ai/review.md` | Senior review history (round 1 + round 2); §5 has accepted/rejected verdicts |

---

## Mandatory constraints (brief requirements — non-negotiable)

- **AWS Glue + Spark** for ETL — no replacing with `delta-rs`/Lambda for the ingest jobs
- **"Run a Glue Job for each dataset"** — per-dataset invocation, not a single multi-dataset session (unless documented optional optimization)
- **Write modular, reusable Spark code** — logic in `src/lakehouse/` library; thin entrypoints
- **Delta Lake** as the table format — ACID, MERGE/upsert, schema enforcement
- **AWS Step Functions** for orchestration (Standard, not Express — ADR-010)
- **GitHub Actions** CI/CD, `main`-branch-scoped

---

## Key decisions already locked (do not re-litigate without updating decision.md)

| ADR | Decision |
|-----|----------|
| ADR-002 | Normalize `.xlsx`/`.csv` → Parquet via **Lambda** (pandas/openpyxl) before Spark |
| ADR-005 | Tables **unpartitioned** now; Z-Order by `order_date`; promote to physical partition at ≥1 GB/partition |
| ADR-006 | DynamoDB: **ledger + watermarks only** (schema registry removed) |
| ADR-010 | Step Functions **Standard** (5-min Express timeout rules it out) |
| ADR-011 | **Lambda** for normalization; **Glue Spark per-dataset** for ETL |
| ADR-015 | Athena reads Delta **natively** (`table_type=DELTA`) — no symlink manifests, no `MSCK REPAIR` |
| ADR-016 | **No Glacier** for raw/archive (small files; 128 KB minimum-size penalty) |
| ADR-017 | KMS CMKs in prod + **`bucket_key_enabled=true`** (~99% KMS API cost reduction) |
| ADR-018 | Stateful buckets as **explicit Terraform resource blocks** + `prevent_destroy` |
| ADR-019 | **DynamicFrames prohibited** for Delta — use Spark DataFrames only |
| ADR-020 | Glue Spark: **fixed 2 workers** (`G.1X`), auto-scaling **off** |
| ADR-021 | S3 bucket policy: **`EnforceHTTPS` + `EnforceKMSEncryption`** Deny statements on every data bucket |

---

## What NOT to do

- **Don't use `DynamicFrame`** for Delta — it silently bypasses the transaction log (ADR-019)
- **Don't set 1 worker** on a Glue Spark job — AWS API hard minimum is 2 (ADR-020)
- **Don't use `G.025X`** worker type for batch — it's streaming-only
- **Don't partition** the Delta tables until a partition would hold ≥1 GB (ADR-005)
- **Don't auto-evolve** schema in prod — changes go through code review + migration (ADR-005)
- **Don't generate symlink manifests** or run `MSCK REPAIR` — Athena v3 reads Delta natively (ADR-015)
- **Don't transition small files to Glacier** — per-object overhead exceeds storage savings (ADR-016)
- **Don't rely on S3 default encryption alone** — add the `EnforceKMSEncryption` Deny policy (ADR-021)
- **Don't store AWS credentials** in code or CI — use GitHub OIDC (ADR-013)
- **Don't commit implementation code in this phase** — planning only; code comes in Sprint 1+

---

## Project structure (planned — not yet created)

```
ecom-lakehouse/
├── CLAUDE.md              ← you are here
├── docs/                  ← all planning docs (current phase deliverables)
├── .ai/review.md          ← architectural review history
├── Instruction.txt        ← original project brief
├── Data/                  ← sample data files (3 datasets)
│
│  (created in implementation phase)
├── src/lakehouse/         ← shared Spark library (schemas, transforms, validation, merge)
├── src/glue_jobs/         ← thin per-dataset entrypoints
├── src/normalize/         ← Lambda normalizer (xlsx/csv → Parquet)
├── infra/                 ← Terraform modules + envs
├── orchestration/         ← Step Functions ASL definition
├── tests/                 ← unit + integration + fixtures
└── .github/workflows/     ← CI/CD (ci.yml + deploy.yml)
```

See `docs/directory_structure.md` for the full annotated tree.

---

## Sprint sequence (dependency order, no dates — solo + agents)

```
S0 Foundations  →  S1 Infrastructure  →  S2 Ingestion/Normalization
→  S3 Transform/Delta (largest)  →  S4 Orchestration
→  S5 Catalog/CI/CD/Hardening  →  S6 Production go-live
```

S3 library work runs locally in parallel with S1/S2 (hermetic Spark tests). See `docs/sprint_planning.md`.

---

## Data profiled (provided sample)

| dataset | file | rows | key notes |
|---------|------|------|-----------|
| products | `Data/products.csv` | 1,000 | dimension; clean; 6 departments |
| orders | `Data/orders_apr_2025.xlsx` | 500 | **xlsx**; all `2025-04-01`; clean |
| order_items | `Data/order_items_apr_2025.xlsx` | 2,768 | **xlsx**; FKs intact; clean sample |

Design for dirty future — validation/dedup/RI enforced regardless.

---

## When stuck

1. Check `docs/reference.md` §4 troubleshooting playbook (symptom → owning doc → external link)
2. Check `docs/decision.md` before re-deciding anything — it may already be an ADR
3. The Design Contract is `docs/architecture.md` §3 — all zone names, schemas, keys are there
