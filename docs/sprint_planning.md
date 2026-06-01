# Sprint Planning & Resource Allocation (Agentic, Solo Operator)

> Cites `master_plan.md` §7. **No calendar timeline** — the project is executed by a
> single engineer with execution agents, so we sequence by **dependency**, not dates
> (per the user's instruction).

## 1. Approach
Work is sliced into **6 dependency-ordered sprints**, each with a clear **Definition of
Done (DoD)** and a **demonstrable artifact**. Within a sprint, tasks are decomposed
**agentically**: each task is a self-contained, verifiable unit an execution agent can
pick up (the same stick-holding/telephone discipline as `terraform.md` §0). "Resource
allocation" here = which **agent role** owns each task (the solo operator wears all hats,
but the work is labeled so it can be delegated to focused agents).

Agent roles: **InfraAgent**, **DataAgent** (Spark/Delta), **OrchestrationAgent**,
**QualityAgent** (tests/validation), **PlatformAgent** (CI/CD/security/observability).

## 2. Sprint backlog

### Sprint 0 — Foundations & contract
*Goal:* lock the contract and scaffold the repo.
- Finalize Design Contract + decision log (this planning phase ✅).
- Scaffold repo structure (`directory_structure.md`); pin deps; pre-commit.
- Stand up local Spark+Delta test harness.
- **Owner:** PlatformAgent + DataAgent. **DoD:** repo builds, `pytest` runs (0 tests OK),
  lint/format gates green.

### Sprint 1 — Infrastructure (Terraform)
*Goal:* all AWS resources provisioned in `dev`.
- `s3_zones`, `dynamodb`, `iam`, `kms`, `observability` modules.
- Remote state + OIDC deploy role.
- **Owner:** InfraAgent. **DoD:** `terraform apply` clean in dev; policy simulation passes
  (`security_iam.md`); buckets encrypted/versioned.

### Sprint 2 — Ingestion & normalization
*Goal:* raw → staging Parquet + ledger, idempotent.
- `normalize_to_parquet` **AWS Lambda** (xlsx/csv) + in-code schema check.
- DynamoDB ledger client + conditional-write idempotency.
- **Owner:** DataAgent. **DoD:** a sample drop produces staging Parquet + a ledger row;
  re-drop is a no-op (`data_handling.md`, `dynamodb_schema.md`).

### Sprint 3 — Transform, validate & Delta MERGE (core value)
*Goal:* the three Delta tables, cleaned/deduped/validated.
- `lakehouse` library: types, derivations, rule engine, dedup, MERGE.
- Quarantine writing + reject-rate gate.
- `OPTIMIZE`/`VACUUM` maintenance job.
- **Owner:** DataAgent + QualityAgent. **DoD:** golden + dirty fixtures pass all tests
  (`testing_strategy.md`); Athena RI/dedup checks = 0.

### Sprint 4 — Orchestration
*Goal:* Step Functions runs the full lifecycle.
- State machine (claim → normalize → validate → load → gate → optimize → catalog →
  athena-validate → archive), retries/timeouts/branches.
- EventBridge trigger + scheduled fallback + backfill `Map`.
- **Owner:** OrchestrationAgent. **DoD:** end-to-end execution reaches `Succeed`; induced
  failure routes to `HandleFailure` + alert; duplicate → `NoOpSucceed`.

### Sprint 5 — Catalog, Athena & CI/CD + hardening + Streamlit UI
*Goal:* queryable + reproducible + observable + user-story-testable.
- Glue Catalog registration + Athena workgroup + validation queries.
- `ci.yml` + `deploy.yml` (OIDC, main-scoped) + smoke test.
- Dashboards + alarms; security/compliance gates.
- **Streamlit UI** (`src/ui/`) — 4 pages: Pipeline Dashboard, Data Explorer, Data Quality,
  Batch Trigger. Local dev only; validates all 8 user stories interactively. See
  `ui_streamlit.md` for page specs and the optional `streamlit-ui-role` for deployment.
- **Owner:** PlatformAgent. **DoD:** PR gates enforced; `main` deploys dev; Athena returns
  expected rows; alarms fire on induced faults; all 8 US acceptance criteria pass in UI.

### Sprint 6 — Production readiness
*Goal:* prod go-live.
- prod env via tfvars + manual-approval promotion.
- Backfill historical data; run go-live runbook (`production_deployment.md`).
- **Owner:** all. **DoD:** prod pipeline green on canary + backfill; runbook + rollback
  validated.

## 3. Dependency graph
```
S0 ─▶ S1 ─▶ S2 ─▶ S3 ─▶ S4 ─▶ S5 ─▶ S6
                 └────────────┘ (S3 testable locally before S4 infra wiring)
```
S3's library work can proceed locally in parallel with S1/S2 infra (hermetic tests), then
integrates once infra exists — the main parallelization lever for a solo operator.

## 4. Resource allocation (effort, not dates)
| sprint | relative effort | risk | critical path? |
|--------|-----------------|------|----------------|
| S0 | S | low | yes |
| S1 | M | med (IAM) | yes |
| S2 | M | med (idempotency) | yes |
| S3 | **L (largest)** | high (correctness) | yes |
| S4 | M | med (branching) | yes |
| S5 | M | med | partial |
| S6 | S | med (prod) | yes |

## 5. Definition of Done (global)
A sprint is done when: code merged via PR with green CI, tests cover the new behavior,
the demonstrable artifact works, and the relevant doc's acceptance criteria are met.

## 6. Agentic execution notes
- Each task ticket carries inputs → action → expected output → verification, so any agent
  can execute without out-of-band context (telephone guardrail).
- Hand-offs go through the repo (PRs, artifacts, ledger) — durable state, not memory.
- **When an agent is stuck**, the first stop is `reference.md` — it maps the symptom to
  the owning doc and the authoritative external reference (no guessing, no telephone drift).
