# Testing Strategy

> Cites `transformation_logic.md`, `data_validation.md`, `cicd_github_actions.md`. Owns
> the test half of Objective **O7**.

## 1. Test pyramid

| level | scope | tools | runs |
|-------|-------|-------|------|
| **Unit** | pure library functions (transforms, validation rules, hashing, dedup) | `pytest`, `chispa` (DataFrame equality), local `pyspark`+`delta-spark` | every PR |
| **Integration** | end-to-end on fixtures against a local Delta warehouse | `pytest` + local Spark + temp Delta dir | every PR |
| **Contract** | code schemas/config match the Design Contract | `pytest` asserting `schemas.py` == documented dtypes | every PR |
| **Infra** | Terraform validity + policy | `terraform validate`, `tflint`, `tfsec`/`checkov` | every PR |
| **Smoke (deploy)** | real AWS canary execution | Step Functions `StartExecution` + Athena query | on `main` deploy |

## 2. Fixtures (golden + deliberately dirty)
Tiny CSV/Parquet fixtures under `tests/data/`:
- **Golden:** small valid slice of each dataset → asserts correct typing/dedup/MERGE.
- **Dirty (one case per rule):** null PK, future timestamp, negative `total_amount`,
  out-of-set `department`, `reordered=2`, duplicate key, **orphan FK** (item→missing
  order/product) → asserts each lands in quarantine with the right reason code.
- **Idempotency:** same batch applied twice → assert table unchanged (row count + hashes).
- **Re-drop changed file:** same key, different checksum → assert upsert updates in place.

## 3. Key test cases (acceptance, mirrors each doc's criteria)
- Validation: every rule in `data_validation.md` §3 has a passing + failing test.
- Dedup: within-batch duplicates collapse to latest deterministically.
- MERGE: insert new / update changed / skip identical (hash) — three assertions.
- Referential integrity: orphan items quarantined; valid items pass.
- Reject-rate gate: >5% bad → job raises `RejectRateExceeded` (non-retryable).
- Idempotency: ledger conditional-write short-circuit (mock DynamoDB via `moto`).
- Backfill: `Map`-style loop over N fixtures yields same result as N sequential runs.

## 4. Local Spark + Delta harness
A `conftest.py` session-scoped `SparkSession` with Delta extensions; tests write to a
`tmp_path` warehouse so they're hermetic and parallelizable. No AWS needed → fast, free,
runs in CI on every PR.

## 5. Data-quality regression
Athena validation queries (`catalog_and_athena.md` §5) double as post-deploy data tests:
RI = 0, dedup = 0, count ≥ ledger `rows_valid`. A breach fails the smoke test.

## 6. Architectural verification tasks (from review `.ai/review.md` §4 — accepted)
Empirical checks to run during implementation to confirm the accepted optimizations hold:
- **Partitioning / small-file impact:** benchmark Athena query time + S3 GET request counts
  for an unpartitioned + Z-Ordered fact table vs a daily-`order_date`-partitioned variant on
  representative volume — confirms the unpartitioned decision (ADR-005) and calibrates the
  promotion threshold.
- **Normalizer benchmark:** process the same ~1.6 MB Excel file with the **Lambda**
  normalizer vs a Glue Python-shell job; compare cold-start, duration, and billed cost —
  confirms ADR-011.
- **Accidental-destroy test:** in a `dev` workspace, attempt `terraform destroy` (or a
  `for_each`-key rename) on a stateful bucket with `prevent_destroy = true` and verify
  Terraform **blocks** the teardown — confirms ADR-018.
- **Athena native Delta:** confirm queries succeed with `table_type=DELTA` and **no**
  manifest/`MSCK` step — confirms ADR-015.

## 7. Coverage & gates
- Library coverage threshold (e.g. ≥ 85%) enforced in CI.
- Mutation/edge sampling on the validation rule engine (highest-risk logic).
- All gates must be green to merge to `main`.

## 8. Acceptance criteria
- Each validation rule + MERGE branch + idempotency path has automated coverage.
- CI fails on any dirty-fixture misroute or coverage drop.
- Smoke test catches a broken deploy before prod promotion.
