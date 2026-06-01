# Master Plan — Lakehouse Architecture for E-Commerce Transactions

> **Phase:** Planning & Documentation only. **No code or infrastructure is created
> in this phase.** Illustrative snippets in these docs are non-binding sketches.
> **Author role:** Senior Data Engineer. **Date:** 2026-05-30.

This is the entry point. Read it first, then `architecture.md` (the Design Contract),
then the sub-plans in the order listed in §6.

---

## 1. Executive Summary

We will deliver a production-grade, medallion-style **Lakehouse on AWS** that ingests
raw e-commerce transactional files from S3, normalizes and validates them, writes
ACID-compliant **Delta Lake** tables to a curated DWH zone, catalogs them for **Athena**,
and runs the whole lifecycle through **AWS Step Functions**, deployed via **GitHub
Actions** CI/CD scoped to `main`.

The work is decomposed using an **agentic approach**: every sub-plan is written so that
a focused execution agent (human or AI) can pick it up, follow self-contained and
verifiable steps, and hand off cleanly to the next — minimizing "telephone-game" drift
(see `terraform.md` for the explicit statement of this working style).

## 2. Objectives (mapped to the brief)

| # | Objective | Success looks like |
|---|-----------|--------------------|
| O1 | Detect new data in S3 raw zone | A new drop triggers the pipeline within minutes (event or scheduled poll). |
| O2 | Clean & transform with Glue + Delta | Validated, typed, deduplicated records produced; rejects quarantined. |
| O3 | Write optimized Delta tables to DWH | 3 optimized (Z-Ordered, unpartitioned at current volume — ADR-005) Delta tables with MERGE/upsert semantics. |
| O4 | Update Glue Catalog for Athena | Tables queryable in Athena immediately after a successful run. |
| O5 | Archive originals after success | Source files moved to `/archived/<dataset>/<batch_id>/`. |
| O6 | Orchestrate with Step Functions | State machine with branching, retries, timeouts, alerting. |
| O7 | CI/CD with GitHub Actions | Lint+test+deploy on `main`; PRs gated; SF/Glue artifacts deployed. |
| O8 (NFR) | Reliability, schema enforcement, freshness | Idempotent re-runs; schema drift rejected; predictable SLA. |

## 3. Synthesized User Stories

The brief does not enumerate user stories, so we synthesize them from the 7 mission
items + "Expected Deliverables." `As a <role>, I want <capability>, so that <value>`:

- **US-1 (Data Engineer):** ingest a new monthly drop with one trigger, so that I don't
  run manual jobs.
- **US-2 (Data Engineer):** re-run a failed/duplicate file safely, so that retries never
  double-count or corrupt tables. *(idempotency)*
- **US-3 (Data Steward):** have null PKs, bad timestamps, and orphan FKs rejected and
  logged, so that the warehouse stays trustworthy.
- **US-4 (Analyst):** query fresh, deduplicated orders/items/products in Athena, so that
  reports reflect the latest drop.
- **US-5 (Analyst):** get fast time-filtered queries, so that Delta data-skipping/Z-Order
  (and physical partitioning once volume warrants it — ADR-005) keeps cost/latency low.
- **US-6 (Platform Owner):** be alerted on pipeline failure with enough context to
  triage, so that issues are caught before stakeholders notice.
- **US-7 (Release Manager):** ship changes only through reviewed, tested CI/CD on `main`,
  so that production is reproducible and auditable.
- **US-8 (Data Engineer):** backfill historical S3 data through the same pipeline, so
  that history and incremental loads share one code path. *(historical integration)*

## 4. Requirement → Document → Acceptance Traceability Matrix

| Req | User story | Owning document(s) | Acceptance criterion |
|-----|-----------|--------------------|----------------------|
| O1 detect | US-1 | `orchestration_stepfunctions.md`, `data_handling.md` | New raw key starts a run; no double-trigger. |
| O2 clean | US-3 | `data_validation.md`, `transformation_logic.md` | Valid rows pass; invalid rows in quarantine with reason. |
| O3 Delta | US-2,US-5 | `delta_lake_design.md`, `transformation_logic.md` | 3 Delta tables, unpartitioned + Z-Order (ADR-005), MERGE upserts, no dups. |
| O4 catalog | US-4 | `catalog_and_athena.md` | Athena `SELECT` returns expected counts post-run. |
| O5 archive | US-1 | `orchestration_stepfunctions.md`, `data_handling.md` | Source moved to archive only after success. |
| O6 orchestrate | US-6 | `orchestration_stepfunctions.md`, `error_handling.md` | SM has retries/timeouts/branches; failure alerts. |
| O7 CI/CD | US-7 | `cicd_github_actions.md`, `testing_strategy.md` | `main` push runs lint+test+deploy; PR gates green. |
| O8 NFR | US-2,US-3,US-5,US-8 | `architecture.md`, `dynamodb_schema.md`, `data_handling.md` | Idempotent re-run; drift rejected; backfill path works. |
| Sec | all | `security_iam.md` | Least-privilege roles; encryption at rest/in transit. |
| Obs | US-6 | `monitoring_observability.md` | Dashboards + alarms for each stage. |
| Repo | US-7 | `directory_structure.md` | Layout supports modular Spark + IaC + CI. |
| Deploy | US-7 | `production_deployment.md` | Documented go-live + rollback runbook. |

## 5. Data Reality (profiled from provided files)

| dataset | file | rows | state today | implication |
|---------|------|------|-------------|-------------|
| products | `products.csv` | 1,000 | clean, unique PK | dimension; small; no partition |
| orders | `orders_apr_2025.xlsx` | 500 | clean, all `2025-04-01` | **`.xlsx`** needs normalization (ADR-002); monthly batch |
| order_items | `order_items_apr_2025.xlsx` | 2,768 | clean, FKs intact | **`.xlsx`**; FK to orders & products enforced |

**Key insight:** data is *currently* clean, but the pipeline must still **enforce**
validation, dedup, and referential integrity — because recurring production drops
(`apr_2025` ⇒ monthly cadence) will not be. We design for the dirty future, not the
clean sample.

## 6. Document Index (recommended reading order)

| # | Document | What it covers |
|---|----------|----------------|
| 0 | `master_plan.md` (this) | Strategy, objectives, user stories, traceability |
| 1 | `architecture.md` | **Design Contract / source of truth** |
| 2 | `decision.md` | Decision log (ADRs) + trade-offs |
| 3 | `directory_structure.md` | Repository layout |
| 4 | `data_handling.md` | Ingestion, xlsx normalization, historical backfill + incremental |
| 5 | `data_validation.md` | Validation rules, rejected-record handling |
| 6 | `transformation_logic.md` | Spark cleaning, dedup, MERGE/upsert |
| 7 | `delta_lake_design.md` | Delta tables, partitioning, OPTIMIZE/VACUUM |
| 8 | `glue_jobs.md` | Glue job design, modular reusable Spark |
| 9 | `dynamodb_schema.md` | Control-plane tables (idempotency ledger / watermarks) |
| 10 | `orchestration_stepfunctions.md` | State machine design |
| 11 | `error_handling.md` | Retries, DLQ, alerting strategy |
| 12 | `catalog_and_athena.md` | Glue Catalog + Athena |
| 13 | `terraform.md` | IaC plan (agentic, terrashark guardrails) |
| 14 | `cicd_github_actions.md` | CI/CD pipelines |
| 15 | `testing_strategy.md` | Unit/integration/data tests |
| 16 | `security_iam.md` | IAM, KMS, network, secrets |
| 17 | `monitoring_observability.md` | Logs, metrics, dashboards, alarms |
| 18 | `sprint_planning.md` | Agentic sprint breakdown + resource allocation |
| 19 | `production_deployment.md` | Go-live runbook + rollback |
| 20 | `reference.md` | **Resource hub — start here when stuck** (internal + external links, troubleshooting playbook) |
| 21 | `ui_streamlit.md` | Streamlit UI plan — 4-page app for validating all 8 user stories |

> **Stuck during execution?** Jump to `reference.md` — it routes any symptom to the owning
> document and the authoritative external reference.

## 7. Execution Strategy (agentic, solo operator)

- Work is sliced into **6 sprints** (see `sprint_planning.md`). No calendar timeline —
  the project is executed by a single engineer + execution agents, so we sequence by
  dependency, not date.
- Each sprint has a clear **definition of done** and produces a demonstrable artifact.
- Build order respects dependencies: **infra → ingestion/normalization → validation/
  transform/Delta → orchestration → catalog/Athena → CI/CD → hardening → go-live.**

## 8. Out of Scope (this planning phase)

No Terraform/HCL, no Glue scripts, no Step Function JSON, no GitHub workflow YAML are
*created* now. They are *specified* here and built in the implementation phase. All
deliverables are `.md` files under `docs/`.
