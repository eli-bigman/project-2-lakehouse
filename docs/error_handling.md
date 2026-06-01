# Error Handling, Retries & Alerting

> Cites `orchestration_stepfunctions.md`, `data_validation.md`, `dynamodb_schema.md`.
> Owns Objective **O6** (failure handling) + US-6.

## 1. Taxonomy of failures

| class | examples | strategy | terminal? |
|-------|----------|----------|-----------|
| **Transient** | Glue throttling, network blip, S3 5xx | retry w/ backoff | no (retry) |
| **Structural** | missing columns, unreadable file, schema drift | fail fast + alert | yes |
| **Data-quality** | reject_rate > threshold | fail run, keep quarantine + alert | yes (until fixed) |
| **Row-level** | null PK, orphan FK, bad value | quarantine row, continue | no (per-row) |
| **Idempotency** | file already processed | no-op succeed | no |
| **Logic/bug** | code exception | fail + alert + ledger.error | yes |

## 2. Retry policy (Step Functions)
Per-task `Retry`:
```json
"Retry": [{
  "ErrorEquals": ["Glue.ConcurrentRunsExceededException","Glue.AWSGlueException",
                  "States.TaskFailed","States.Timeout","Lambda.TooManyRequestsException"],
  "IntervalSeconds": 30, "BackoffRate": 2.0, "MaxAttempts": 3, "JitterStrategy": "FULL"
}]
```
- Glue's own retry = 0 (ADR/`glue_jobs.md`) so SF owns retry semantics centrally.
- Distinguish retryable vs non-retryable: structural/data-quality errors are thrown as
  **non-retryable** custom error names (e.g. `SchemaDriftError`, `RejectRateExceeded`) so
  `Retry` skips them and `Catch` routes straight to `HandleFailure`.

## 3. Timeouts
- Glue job timeout (e.g. 30 min) + matching SF task `TimeoutSeconds` (+buffer).
- SF execution `TimeoutSeconds` caps the whole run (e.g. 2 h incl. backfill `Map`).
- Lambda timeouts sized to work (normalization < 5 min; claim/archive < 1 min).

## 4. Catch & the failure sink
Every task `Catch`es to **`HandleFailure`**, which:
1. Writes structured error to CloudWatch Logs (with `batch_id`, state name, cause).
2. Sets ledger `status=FAILED`, `error=<cause>` (idempotency-safe).
3. Publishes to **SNS topic** `ecom-lakehouse-alerts-{env}` (email/Slack/PagerDuty sub).
4. Emits a CloudWatch custom metric `pipeline_failures` (drives an alarm).
5. Transitions to `Fail` with a clear error name for the execution history.

## 5. Dead-letter & poison handling
- **EventBridge → Step Functions** failures (e.g. throttled `StartExecution`) go to an
  **SQS DLQ**; a scheduled re-drive Lambda retries them.
- Files that fail repeatedly (`FAILED` N times in ledger) are flagged `POISON` and
  excluded from auto-retry, alerting a human (prevents infinite retry loops).
- Quarantined rows are never lost — they sit in the quarantine zone for replay after a
  fix.

## 6. Alerting design (US-6)
- One SNS topic per env; severities encoded in the message (`CRITICAL` pipeline failure,
  `WARN` reject-rate elevated / unknown department, `INFO` backfill complete).
- Alert payload includes: env, dataset, `batch_id`, failing state, cause, ledger link,
  CloudWatch Logs Insights deep-link → fast triage.
- Alarms (CloudWatch) on: pipeline failure count, reject-rate breach, freshness SLA miss
  (no successful load within expected window), Glue job duration anomalies, DLQ depth > 0.

## 7. Graceful degradation
Optional steps (`Optimize`, `UpdateCatalog`, `AthenaValidate`) catch-and-continue by
default (configurable) so a non-critical hiccup doesn't fail a good data load — but the
degradation is logged + a `WARN` alert is sent (never silent).

## 8. Idempotent recovery
Because of ledger + Delta MERGE (ADR-007), the standard recovery is **just re-run the
execution** — already-done work is skipped and partial work is corrected. No manual
cleanup of half-written tables.

## 9. Acceptance criteria
- Transient errors retry and succeed; structural errors fail fast without retry.
- Every failure produces a `FAILED` ledger row + SNS alert + metric.
- Re-running a failed execution recovers without duplicates.
- DLQ depth > 0 raises an alarm.
