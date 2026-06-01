# Production Deployment & Go-Live Runbook

> Cites `cicd_github_actions.md`, `terraform.md`, `delta_lake_design.md`,
> `monitoring_observability.md`. Owns the "production deployment" coverage requirement.

## 1. Promotion model
`dev` (auto on `main`) → `prod` (**manual approval** via GitHub Environment, ADR/`cicd`).
Same Terraform modules; env differs only by `tfvars`. No manual console changes in prod —
everything through CI/CD (reproducibility, US-7).

## 2. Pre-go-live checklist
- [ ] All sprint DoDs met; every doc's acceptance criteria green.
- [ ] `terraform plan` clean on prod (no drift).
- [ ] IAM policy simulation: zone isolation verified (`security_iam.md`).
- [ ] Buckets encrypted/versioned/public-access-blocked; KMS keys + policies in place.
- [ ] DynamoDB PITR on; backups configured.
- [ ] SNS alert subscriptions confirmed; dashboards live; alarms tested
      (`monitoring_observability.md`).
- [ ] Smoke test passes on a prod canary file.
- [ ] Runbook + rollback rehearsed in dev.

## 3. Go-live sequence
1. **Tag** the release commit (immutable artifacts already in artifacts bucket).
2. **Apply infra** to prod (approved): `terraform apply` prod workspace.
3. **Deploy definitions:** register Step Functions ASL + Glue job pointers.
4. **Canary:** drop a tiny synthetic file into prod `raw`; confirm full path to `Succeed`,
   ledger `ARCHIVED`, Athena returns rows, alarms quiet.
5. **Backfill history:** run the backfill `Map` over historical S3 keys, oldest→newest,
   bounded concurrency (`data_handling.md` §4.2); monitor reject-rate + freshness.
6. **Cutover:** enable the EventBridge trigger for live drops.
7. **Watch:** observe dashboards for the first real drop; confirm freshness SLA met.

## 4. Rollback strategy
Layered, because infra and data fail differently:
- **Code/definition rollback:** re-deploy the previous git-tagged artifacts + ASL (CI/CD
  is the rollback mechanism — no hand edits).
- **Infra rollback:** `terraform apply` the previous known-good revision;
  `prevent_destroy` protects stateful stores from accidental teardown.
- **Data rollback:** **Delta time travel** — `RESTORE <table> VERSION AS OF <n>` to undo a
  bad batch (retention ≥7d, `delta_lake_design.md` §6). Quarantine + ledger let you replay
  after a fix.
- **Trigger kill-switch:** disable the EventBridge rule to halt intake while
  investigating; in-flight executions drain or are stopped.

## 5. Operational runbook (steady state)
- **Normal drop:** event → SF run → Athena fresh; nothing to do.
- **Failed run:** alert fires → open ledger row + Logs Insights (by `batch_id`) → fix root
  cause → **re-run the execution** (idempotent; no manual cleanup).
- **Elevated reject-rate:** inspect quarantine table → if upstream data issue, notify
  source owner; if rule too strict, adjust rule via PR (versioned).
- **Missing drop (freshness alarm):** chase the source; no data ≠ pipeline failure.
- **Schema change request:** update the in-code schema (`src/lakehouse/schemas.py`) + a
  Delta migration via PR; never auto-evolve prod (`delta_lake_design.md` §4).

## 6. Maintenance cadence
- Post-load `OPTIMIZE ZORDER BY (...)` (table-wide; tables are unpartitioned — ADR-005);
  weekly `VACUUM` (7-day retention).
- Periodic cost review (Glue DPU-hours, Athena bytes scanned, storage tiers).
- Quarterly IAM/access review; dependency + Delta/Glue version upgrades via PR + tests.

## 7. DR & backup
- S3 versioning + (optional) cross-region replication for `dwh`/`archive`.
- DynamoDB PITR. Terraform state bucket versioned + locked.
- Recovery objective: rebuild from `raw`/`archive` via the same pipeline if `dwh` is lost
  (raw is the source of truth — another reason raw is immutable).

## 8. Acceptance criteria
- Prod canary + historical backfill complete green.
- A rehearsed rollback (code, infra, data) restores a known-good state.
- Steady-state runbook actions are documented and idempotent.
