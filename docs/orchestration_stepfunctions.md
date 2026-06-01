# Orchestration — AWS Step Functions State Machine

> Cites Design Contract + `data_handling.md`, `glue_jobs.md`, `error_handling.md`. Owns
> Objectives **O1, O5, O6**. ADR-010 (Standard workflow).

## 1. Responsibilities (brief's orchestration requirements)
1. Detect new file arrival in S3 (simulate trigger). 2. Run a Glue job (Delta) per
dataset. 3. Archive on success. 4. Log error + alert on failure. 5. Optional Glue
Crawler. 6. Optional Athena validation query. Plus failure handling, timeouts, branching.

## 2. Trigger (O1)
- **Primary:** S3 → **EventBridge** "Object Created" rule on the raw bucket → starts a
  Step Functions execution with the object key as input.
- **Fallback / "simulate":** a scheduled EventBridge rule (or manual `StartExecution`)
  enumerating raw keys — satisfies the brief's "simulate trigger" and supports backfill.
- **Idempotent admission:** first task claims the file in the ledger (ADR-007); if already
  processed, the machine short-circuits to `Succeed (NoOp)`.

## 3. State machine design (Standard)

```
StartExecution(input: {raw_key})
        │
        ▼
[ClaimFile]  Lambda → DynamoDB conditional put
        │  ├─ already processed ─▶ [NoOpSucceed]
        ▼
[Normalize]  AWS Lambda (xlsx/csv → Parquet)               (Retry, Catch→[HandleFailure])
        │
        ▼
[ValidateSchema]  Lambda: compare columns to in-code schema (lakehouse.schemas)
        │  ├─ structural drift ─▶ [HandleFailure]
        ▼
[LoadDatasets]  (per-batch ordering: products → orders → order_items)
        │   Sequential chain OR Map over a single dataset depending on trigger
        ├─ [IngestProducts]   Glue Spark + Delta MERGE     (Retry, Catch)
        ├─ [IngestOrders]     Glue Spark + Delta MERGE     (Retry, Catch)
        └─ [IngestOrderItems] Glue Spark + Delta MERGE     (Retry, Catch)
        │
        ▼
[QualityGate]  Choice: reject_rate <= threshold ?
        │  ├─ false ─▶ [HandleFailure] (quarantine kept, alert)
        ▼
[Optimize]  Glue: OPTIMIZE + ZORDER (compact files)      (optional, Catch→continue)
        │
        ▼
[UpdateCatalog]  Glue Crawler OR explicit register        (optional, ADR-015)
        │
        ▼
[AthenaValidate]  Athena: SELECT COUNT(*) sanity check    (optional)
        │  ├─ count mismatch ─▶ [HandleFailure]
        ▼
[Archive]  Lambda/Glue: move raw → archive/<dataset>/<batch_id>/, ledger=ARCHIVED
        │
        ▼
[Succeed]

[HandleFailure]  log to CloudWatch + ledger.status=FAILED + SNS alert ─▶ [Fail]
```

## 4. Branching logic (brief: "branching logic")
- `ClaimFile` → NoOp vs proceed (idempotency).
- `ValidateSchema` → fail vs proceed (structural drift). *Defense-in-depth, by design:*
  the Lambda normalizer already rejects missing/extra columns at write time; this state
  re-confirms the column set/dtypes against the in-code schema before the per-dataset Spark
  fan-out, so a malformed staging artifact can never reach the MERGE. Kept deliberately as
  a cheap explicit gate rather than folded into normalization.
- `QualityGate` (`Choice`) → fail vs proceed (reject-rate).
- `AthenaValidate` → fail vs proceed (presence check).
- Optional steps (`Optimize`, `UpdateCatalog`, `AthenaValidate`) use `Catch` to degrade
  gracefully (a crawler hiccup shouldn't fail an otherwise-good load — configurable).

## 5. Failure handling, retries, timeouts (brief requirement; detail in `error_handling.md`)
- Each Glue task: `Retry` with exponential backoff (`IntervalSeconds: 30`,
  `BackoffRate: 2.0`, `MaxAttempts: 3`) on `Glue.AWSGlueException`,
  `States.TaskFailed`, throttling.
- Each task: `TimeoutSeconds` aligned to the Glue job timeout (+buffer).
- Execution-level `TimeoutSeconds` caps the whole run.
- Every task: `Catch` → `HandleFailure` so no failure is silent.

## 6. Map for backfill (O8)
For historical backfill, the entry point is a `Map` state over a list of raw keys with
`MaxConcurrency` (e.g. 3) — each iteration runs the same sub-flow (Normalize → Load →
Archive). Same definition, different input shape — "seamless" backfill
(`data_handling.md` §4.2).

## 7. Why Standard, not Express (ADR-010)
Glue jobs run minutes; we need exactly-once, full execution history, and native service
integrations + retry/catch. Express targets high-volume short events — wrong fit; volume
is monthly.

## 8. Definition management
- The ASL definition lives in `orchestration/state_machine.asl.json`, **templated by
  Terraform** (job names/ARNs injected per env) — deployed via CI/CD on `main`
  (`cicd_github_actions.md`). Satisfies brief's "Deploy Step Function definition as
  JSON/YAML."

## 9. Acceptance criteria
- A raw drop drives the full path to `Archive` + `Succeed`.
- A duplicate drop ends at `NoOpSucceed`.
- An induced Glue failure retries, then routes to `HandleFailure` with an SNS alert and a
  `FAILED` ledger row.
- Backfill `Map` processes N files with bounded concurrency.
