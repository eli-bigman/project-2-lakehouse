# Repository / Directory Structure

> Cites the Design Contract in `architecture.md`. Defines the implementation-phase repo
> layout so modular Spark, IaC, orchestration, and CI/CD have a home. **Not created in
> this phase** — specified here.

## 1. Design goals

- **Modular & reusable Spark** (brief requirement): shared transforms in a package,
  thin per-dataset entrypoints.
- **Separation of concerns**: `src/` (app), `infra/` (Terraform), `orchestration/`
  (Step Functions), `.github/` (CI/CD), `tests/`, `docs/`.
- **Reproducible**: everything deployable is in-repo and versioned.

## 2. Proposed layout

```
ecom-lakehouse/
├── README.md
├── docs/                              # ← this planning framework (current phase)
│   ├── master_plan.md
│   ├── architecture.md
│   ├── decision.md
│   └── … (all sub-plans)
│
├── src/                               # application code (implementation phase)
│   ├── normalize/                     # xlsx/csv → Parquet (AWS Lambda; pandas/openpyxl)
│   │   └── normalize_to_parquet.py
│   ├── glue_jobs/                     # Spark entrypoints (one thin script per dataset)
│   │   ├── ingest_products.py
│   │   ├── ingest_orders.py
│   │   └── ingest_order_items.py
│   ├── lakehouse/                     # reusable library (the heart of "modular Spark")
│   │   ├── __init__.py
│   │   ├── config.py                  # zone names, table specs ← mirrors Design Contract
│   │   ├── schemas.py                 # StructType per dataset (single source in code)
│   │   ├── io.py                      # read Parquet / Delta read+write helpers
│   │   ├── validation.py              # rule engine (see data_validation.md)
│   │   ├── transforms.py              # typing, dedup, audit cols
│   │   ├── merge.py                   # Delta MERGE/upsert helpers
│   │   ├── ledger.py                  # DynamoDB ledger/watermark client
│   │   └── logging_utils.py           # structured logging + metrics emit
│   └── athena/
│       └── validation_queries.sql     # post-load presence/quality checks
│
├── infra/                             # Terraform (see terraform.md)
│   ├── modules/
│   │   ├── s3_zones/
│   │   ├── glue/
│   │   ├── stepfunctions/
│   │   ├── dynamodb/
│   │   ├── iam/
│   │   ├── athena_catalog/
│   │   └── observability/
│   ├── envs/
│   │   ├── dev/    (backend.tf, main.tf, dev.tfvars)
│   │   └── prod/   (backend.tf, main.tf, prod.tfvars)
│   └── README.md
│
├── orchestration/
│   └── state_machine.asl.json         # Step Functions definition (templated by TF)
│
├── tests/                             # see testing_strategy.md
│   ├── unit/                          # pytest + chispa on transforms/validation
│   ├── integration/                   # local Spark+Delta end-to-end on sample data
│   └── data/                          # tiny fixtures (golden + intentionally-dirty)
│
├── .github/
│   └── workflows/
│       ├── ci.yml                     # lint + unit/integration tests (PR + main)
│       └── deploy.yml                 # terraform + artifact deploy (main only)
│
├── scripts/                           # local dev helpers (make targets, fixture gen)
├── pyproject.toml / requirements.txt  # pinned deps (pyspark, delta-spark, boto3, …)
├── Makefile                           # fmt, lint, test, plan, deploy targets
└── .pre-commit-config.yaml            # black, isort, flake8, tflint, detect-secrets
```

## 3. Why this shape

- `src/lakehouse/` is **importable and unit-testable** without AWS; Glue entrypoints stay
  thin (parse args → call library). This is what makes Spark code "modular & reusable."
- `config.py` + `schemas.py` are the **code-side mirror** of the Design Contract; a test
  asserts they match the documented schemas so the docs and code can't silently diverge.
- `infra/modules` + `infra/envs` gives dev/prod parity with per-env `tfvars`.
- Tests sit beside fixtures including **deliberately dirty data** to prove validation.

## 4. Conventions

- Python: `black` + `isort` + `flake8`, type hints, module docstrings.
- Terraform: `terraform fmt`, `tflint`, `terraform validate` in CI.
- Commits: Conventional Commits; PR required to merge to `main`.
- Secrets: none in repo (`detect-secrets` pre-commit; OIDC for AWS — see `security_iam.md`).
