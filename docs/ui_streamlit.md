# Streamlit UI — Plan for User Story Validation

> **Status:** Planning only. No code exists yet. Added to satisfy the need to test user
> stories end-to-end with a visible interface. Built in **Sprint 5** alongside CI/CD.
> **Not part of the original brief** — an internal observability/testing tool, not a
> customer-facing product.

---

## 1. Purpose & scope

The UI provides a **human-readable window** into the pipeline for testing, monitoring,
and demonstrating each user story without needing to navigate the AWS console. It runs
locally against the `dev` environment using the developer's AWS credentials; an optional
deployed version targets `prod`.

**Stack:** Python + **Streamlit** + `boto3` + `awswrangler` (PyAthena alternative) +
`plotly` for charts.

**Not in scope:** authentication, multi-tenancy, public access, custom domain. This is
a developer/stakeholder tool, not a customer-facing app.

---

## 2. User stories the UI validates

| Page | User story | What the UI demonstrates |
|------|-----------|--------------------------|
| Pipeline Dashboard | **US-6** (alert on failure), **US-1** (confirm trigger fired) | Last run status, freshness lag, executions list, alarm state |
| Data Explorer | **US-4** (query Athena), **US-5** (fast queries via Z-Order) | Browse dim_products, fct_orders, fct_order_items; filter by date/dept; show query duration |
| Data Quality | **US-3** (bad rows quarantined), **US-2** (idempotency visible) | Reject rates by batch, quarantine record browser with reason codes, ledger status per file |
| Batch Trigger | **US-1** (one-trigger ingest), **US-8** (backfill) | Upload a file → trigger SF execution; run historical backfill by date range |

---

## 3. Application pages

### Page 1 — Pipeline Dashboard
Entry page. Shows the health of the last N pipeline runs.

**Components:**
- **Header banner:** environment label (dev/prod), last-refresh timestamp
- **Freshness cards** (3): one per dataset — "Last loaded: X ago" (from DynamoDB watermarks)
- **Recent executions table:** last 10 Step Functions executions — status, duration, dataset, batch_id (from `states:ListExecutions`)
- **Alarm status panel:** CloudWatch alarms — `pipeline_failures`, `reject_rate`, `freshness_lag_hours`, `dlq_depth`
- **Reject-rate trend chart:** line chart per dataset over the last N batches (from DynamoDB ledger)

### Page 2 — Data Explorer
Query the curated Delta tables through Athena.

**Components:**
- **Dataset selector:** dim_products / fct_orders / fct_order_items
- **Filter controls:** date range (`order_date`), department (products), reordered flag (items)
- **Query builder:** generates parameterized SQL; enforces `LIMIT` (cost guardrail from `UI_ATHENA_MAX_ROWS`)
- **Results table:** paginated; download as CSV
- **Query stats bar:** bytes scanned, execution time, estimated cost — demonstrates US-5 (Z-Order effectiveness)
- **RI check queries:** one-click run of the validation queries from `catalog_and_athena.md` §5

### Page 3 — Data Quality
Inspect rejected records and pipeline health metrics.

**Components:**
- **Ledger browser:** table of all ledger rows by file_key, status, rows_in/valid/rejected, reject_rate — sortable, filterable by status/dataset
- **Quarantine record viewer:** query `quarantine_<dataset>` Athena table; shows `_reject_reasons` array, source file, batch_id
- **Reject-reason breakdown:** bar chart of top reject reasons per dataset
- **Idempotency test:** pick a `file_key`, click "Re-trigger" → shows ledger short-circuit (status stays ARCHIVED, no new run)

### Page 4 — Batch Trigger
Manually upload a file and watch the pipeline run.

**Components:**
- **File uploader:** drag-and-drop CSV/XLSX → validates filename pattern → uploads to `S3_RAW_BUCKET/<dataset>/<yyyy>/<mm>/`
- **Trigger button:** calls `states:StartExecution` with the S3 key
- **Live execution viewer:** polls the execution ARN every 5 s; shows state transitions as they happen
- **Backfill panel:** specify dataset + date range → lists matching raw keys → starts a backfill `Map` execution
- **Guard:** `UI_ENABLE_TRIGGER=false` hides this page (prevents accidental triggering in prod)

---

## 4. Architecture

```
Developer's browser
      │
      ▼
┌─────────────────┐  boto3 / awswrangler
│  Streamlit app  │ ─────────────────────▶  Amazon Athena  (Data Explorer queries)
│  src/ui/app.py  │ ─────────────────────▶  AWS Step Functions  (trigger/status)
│                 │ ─────────────────────▶  DynamoDB  (ledger / watermarks)
│                 │ ─────────────────────▶  CloudWatch  (alarms / metrics)
│                 │ ─────────────────────▶  S3  (file upload → raw bucket)
└─────────────────┘
```

All AWS calls use the **developer's local credentials** (AWS_PROFILE / SSO) for local dev.
A deployed version uses a dedicated `streamlit-ui-role` (see §6).

---

## 5. Planned file structure

```
src/ui/
├── app.py                  # Streamlit entry point; sets page config + navigation
├── pages/
│   ├── 1_pipeline_dashboard.py
│   ├── 2_data_explorer.py
│   ├── 3_data_quality.py
│   └── 4_batch_trigger.py
├── components/
│   ├── athena.py           # awswrangler query helpers with cost guardrails
│   ├── ledger.py           # DynamoDB ledger / watermark reads
│   ├── stepfunctions.py    # execution list, start, poll
│   └── cloudwatch.py       # alarm status, metric history
└── config.py               # reads from .env / environment variables
```

---

## 6. IAM role for deployed Streamlit (optional)

If you deploy the app to EC2/ECS/App Runner, attach a `streamlit-ui-role` with:

| Permission | Resources | Reason |
|-----------|-----------|--------|
| `athena:StartQueryExecution`, `athena:GetQueryExecution`, `athena:GetQueryResults` | workgroup ARN | Data Explorer queries |
| `s3:GetObject`, `s3:PutObject` | athena-results bucket | Query output |
| `glue:GetDatabase`, `glue:GetTable`, `glue:GetTables` | catalog DB | Schema awareness |
| `dynamodb:GetItem`, `dynamodb:Query`, `dynamodb:Scan` | ledger + watermarks tables (read-only) | Data Quality / Dashboard |
| `states:ListExecutions`, `states:DescribeExecution` | state machine ARN | Pipeline Dashboard |
| `states:StartExecution` | state machine ARN | Batch Trigger page only |
| `s3:PutObject` | raw bucket (trigger page only) | File upload |
| `cloudwatch:GetMetricStatistics`, `cloudwatch:DescribeAlarms` | namespace `EcomLakehouse` | Dashboard alarms |

Add this role to `infra/modules/iam/` and `docs/security_iam.md` §2 during implementation.

---

## 7. Local dev quick-start (implementation phase)

```bash
# Install
pip install streamlit awswrangler plotly boto3

# Configure (copy and fill .env.example → .env first)
source .env

# Run
streamlit run src/ui/app.py

# The app opens at http://localhost:8501
# It reads AWS_PROFILE / AWS_REGION from your environment
```

---

## 8. Acceptance criteria (per user story)

- **US-1:** Uploading a file via Batch Trigger page starts a successful execution visible in the Dashboard.
- **US-2:** Uploading the same file a second time shows "already processed" in the ledger; no new execution starts.
- **US-3:** A file with dirty rows shows non-zero reject count in Data Quality; each bad row has a human-readable reason.
- **US-4:** Data Explorer returns query results from all three Athena tables with no catalog errors.
- **US-5:** Query stats bar shows sub-second Athena execution and bytes-scanned consistent with Z-Order effectiveness.
- **US-6:** Dashboard shows ALARM state when a `pipeline_failures` metric is manually raised.
- **US-8:** Backfill panel successfully triggers a multi-file backfill with progress visible in the Dashboard.

---

## 9. Sprint placement

Implemented in **Sprint 5** (Catalog/CI/CD/Hardening). Depends on:
- Sprint 3: Delta tables + validation (Data Explorer, Quality pages need data)
- Sprint 4: Step Functions deployed (Trigger/Dashboard pages need the state machine)

The UI itself has zero infrastructure dependencies (no new AWS resources for local dev).
The deployed version adds one IAM role and optionally one ECS task / App Runner service.
