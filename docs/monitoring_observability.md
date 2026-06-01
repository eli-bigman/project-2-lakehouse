# Monitoring & Observability

> Cites `error_handling.md`, `data_validation.md`, `dynamodb_schema.md`. Cross-cutting NFR
> (freshness/reliability) + US-6.

## 1. Pillars
- **Logs** — structured (JSON) logs from Lambda/Glue/Step Functions → CloudWatch Logs,
  every line tagged with `batch_id`, `dataset`, `env`, state name.
- **Metrics** — custom CloudWatch metrics emitted per batch.
- **Traces/lineage** — `batch_id` threads through ledger → audit columns → archive path →
  logs, giving end-to-end lineage.
- **Alarms/alerts** — CloudWatch alarms → SNS (`error_handling.md` §6).

## 2. Custom metrics (namespace `EcomLakehouse`)
| metric | dim | use |
|--------|-----|-----|
| `rows_in` / `rows_valid` / `rows_rejected` | dataset, env | volume + DQ trend |
| `reject_rate` | dataset, env | DQ gate + alarm (>5%) |
| `dedup_collapsed` | dataset | dup pressure |
| `fk_orphans` | dataset | RI health |
| `pipeline_failures` | env | reliability alarm |
| `batch_duration_seconds` | dataset | perf/regression |
| `freshness_lag_hours` | dataset | SLA: time since last successful load |
| `dlq_depth` | — | stuck triggers |

## 3. Dashboards
A CloudWatch dashboard per env:
- Pipeline health (success/failure counts, last run status from ledger).
- Data quality (reject_rate, orphans, dedup over time).
- Freshness (lag per dataset vs SLA line).
- Performance (Glue duration, DPU-hours, Athena bytes scanned/cost).
- Backfill progress (Map iteration completion).

## 4. Alarms (→ SNS)
| alarm | condition | severity |
|-------|-----------|----------|
| Pipeline failure | `pipeline_failures > 0` (1 period) | CRITICAL |
| Reject-rate breach | `reject_rate > 0.05` | WARN/CRITICAL |
| Freshness SLA miss | `freshness_lag_hours > <SLA>` | CRITICAL |
| DLQ not empty | `dlq_depth > 0` | CRITICAL |
| Glue duration anomaly | > N× baseline | WARN |
| Athena cost | bytes scanned > budget | WARN |

## 5. Logging standards
- One structured event per state transition; no PII in logs.
- Log retention 30–90d (cost-tuned); export to S3 for long-term audit if required.
- **CloudWatch Logs Insights** saved queries for triage (filter by `batch_id`), linked in
  alert payloads.

## 6. Health & freshness as first-class signals
`freshness_lag_hours` derived from the DynamoDB watermark (`dynamodb_schema.md`) — a load
that never arrives is as important as one that fails. The freshness alarm catches a
*missing* monthly drop, not just a broken job.

## 7. Acceptance criteria
- Every batch emits the documented metrics; dashboard reflects the latest run.
- Inducing a failure / high reject-rate / stale watermark each fires the right alarm.
- Logs are queryable by `batch_id` end-to-end.
