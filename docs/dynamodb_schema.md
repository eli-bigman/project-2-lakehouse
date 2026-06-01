# DynamoDB Control-Plane Schema Design

> Cites Design Contract (`architecture.md` §3.6). Implements ADR-006/ADR-007. DynamoDB is
> **not** in the brief's core-services list — it is introduced deliberately as a
> control plane; justification is in `decision.md` ADR-006.

## 1. Purpose

DynamoDB stores **pipeline control state**, never analytical data. It gives cheap,
serverless, strongly-consistent, conditional writes for:
1. **Idempotency / exactly-once triggering** (ingestion ledger).
2. **Watermarks** (what's the latest processed period per dataset).

> **Schema registry removed (review 1.5 / ADR-006 revised).** Expected schemas live in
> code (`src/lakehouse/schemas.py`, mirroring Design Contract §3.3) and are enforced
> structurally at normalization and definitively by Delta on write. A DynamoDB registry
> would duplicate Delta's native enforcement and add state + IAM surface for no net gain,
> since schema changes are gated through code review (no auto-evolve). Two tables remain.

## 2. Tables

### 2.1 `ecom_lakehouse_ingestion_ledger_{env}`
One item per source file processed. The heart of idempotency (ADR-007).

| attribute | type | notes |
|-----------|------|-------|
| `file_key` (**PK**) | S | S3 raw key, globally unique per drop |
| `batch_id` | S | `{dataset}-{yyyymmdd}-{uuid}` |
| `dataset` | S | products / orders / order_items |
| `source_checksum` | S | sha-256 of raw bytes (detect re-drop of changed file) |
| `status` | S | `NORMALIZED` → `VALIDATED` → `LOADED` → `ARCHIVED` / `FAILED` / `REJECTED_FORMAT` |
| `rows_in` / `rows_valid` / `rows_rejected` | N | metrics snapshot |
| `reject_rate` | N | gate result |
| `staging_uri` / `archive_uri` | S | lineage |
| `created_at` / `updated_at` | S (ISO8601) | timing |
| `error` | S | last error message if FAILED |
| `ttl` | N | optional expiry for terminal-state items (e.g. 18 mo) |

**Idempotency pattern** — conditional put prevents double-processing:
```python
table.put_item(
  Item={"file_key": key, "status": "NORMALIZED", ...},
  ConditionExpression="attribute_not_exists(file_key) OR #s IN (:failed)",
  ExpressionAttributeNames={"#s": "status"},
  ExpressionAttributeValues={":failed": "FAILED"},
)  # ConditionalCheckFailedException ⇒ already processed ⇒ orchestrator no-ops
```
A re-dropped identical file (same checksum, `status=ARCHIVED`) is skipped. A re-drop with
a *different* checksum (correction) is allowed to re-process and MERGE-upsert.

**GSI:** `dataset-status-index` (PK `dataset`, SK `status`) to query "all FAILED files for
orders" during triage/backfill.

### 2.2 `ecom_lakehouse_watermarks_{env}`
| attribute | type | notes |
|-----------|------|-------|
| `dataset` (**PK**) | S | one row per dataset |
| `last_processed_period` | S | e.g. `2025-04` |
| `last_batch_id` | S | traceability |
| `last_loaded_at` | S | freshness signal (feeds monitoring) |

Used for freshness SLA checks and to drive incremental/backfill decisions.

> **(Removed) `ecom_lakehouse_schema_registry_{env}`** — see the note in §1. Structural
> drift is now detected at normalization by comparing the incoming column set to the
> in-code schema (`src/lakehouse/schemas.py`); a mismatch is a hard fail + alert
> (`data_validation.md` L0, ADR-008). Delta enforces types on write as the final gate.

## 3. Capacity, consistency, security
- **Billing:** on-demand (PAY_PER_REQUEST) — traffic is tiny and bursty (monthly).
- **Consistency:** ledger reads use **strongly consistent** reads for the idempotency
  check.
- **Encryption:** SSE with KMS (`security_iam.md`).
- **Backup:** point-in-time recovery (PITR) on the ledger (control state is valuable).
- **IAM:** Glue/Lambda/Step Functions roles get least-privilege item-level access only to
  these tables (`security_iam.md`).

## 4. Why not just S3 manifests / Delta alone?
- S3 has no cheap conditional-write "claim" primitive → race conditions on concurrent
  triggers. DynamoDB conditional writes give atomic claim/skip.
- Delta MERGE gives *row-level* idempotency but cannot answer "is this whole file already
  archived?" at orchestration time. We use **both layers** (ADR-007).

## 5. Acceptance criteria
- Re-triggering a processed file results in a conditional-check failure → orchestrator
  no-ops (verified in integration tests).
- Ledger row transitions through the documented status lifecycle.
- Watermark advances only on successful archival.
