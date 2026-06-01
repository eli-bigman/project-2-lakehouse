# Data Validation & Rejected-Record Handling

> Cites Design Contract (`architecture.md` §3.3). Owns Objective **O2** (validation half)
> and US-3. Implements the brief's "Validation Rules … Log rejected records."

## 1. Philosophy (ADR-008)

**Reject-and-quarantine, not fail-fast for data rows.** A single bad row must not block a
monthly load. Structural problems (missing columns, unreadable file, schema-fingerprint
mismatch) **do** fail the run. Row-level problems are filtered to **quarantine** with a
machine-readable reason; the valid subset proceeds. A run is failed only if the reject
rate exceeds a configurable threshold (default **5%**).

Validation runs **inside the Spark job**, after typing and before the Delta MERGE.

## 2. Validation layers

| layer | when | failure mode |
|-------|------|--------------|
| **L0 Structural** | normalization | hard fail (missing/extra columns vs the in-code schema `lakehouse.schemas`, unreadable) |
| **L1 Schema/type** | Spark cast | uncastable values → quarantine row |
| **L2 Field rules** | Spark | per-column constraint violations → quarantine row |
| **L3 Uniqueness/dedup** | Spark | duplicate keys collapsed (kept = latest) |
| **L4 Referential integrity** | Spark | orphan FK → quarantine row |
| **L5 Aggregate/threshold** | Spark | reject-rate > threshold → fail run |

## 3. Rule catalog (per Design Contract)

### `dim_products`
| rule | column | check | severity |
|------|--------|-------|----------|
| P1 | `product_id` | not null, int, > 0 | quarantine |
| P2 | `product_id` | unique within batch | dedup |
| P3 | `department_id` | not null, int | quarantine |
| P4 | `department` | not null; ∈ {Books,Sports,Toys,Home,Clothing,Electronics} | quarantine* |
| P5 | `product_name` | not null, non-empty trimmed | quarantine |

\*Unknown department → quarantine but alert (may signal a new valid category → update the
allowed-values list in the in-code schema via PR).

### `fct_orders`
| rule | column | check | severity |
|------|--------|-------|----------|
| O1 | `order_id` | not null, bigint, > 0 | quarantine |
| O2 | `order_id` | unique within batch | dedup |
| O3 | `order_num` | not null, int | quarantine |
| O4 | `user_id` | not null, bigint | quarantine |
| O5 | `order_timestamp` | parses to timestamp; not in the future; ≥ 2020-01-01 | quarantine |
| O6 | `total_amount` | not null; decimal(10,2); ≥ 0; ≤ sane max (e.g. 1e6) | quarantine |
| O7 | `order_date` | equals `date(order_timestamp)`; valid date | quarantine |

### `fct_order_items`
| rule | column | check | severity |
|------|--------|-------|----------|
| I1 | `id` | not null, bigint, > 0, unique | quarantine/dedup |
| I2 | `order_id` | not null | quarantine |
| I3 | `product_id` | not null | quarantine |
| I4 | `reordered` | ∈ {0,1} | quarantine |
| I5 | `add_to_cart_order` | int ≥ 1 | quarantine |
| I6 | `days_since_prior_order` | int 0–365 (observed 0–30) | quarantine* |
| I7 | `order_id` | **FK** exists in `fct_orders` | quarantine (orphan) |
| I8 | `product_id` | **FK** exists in `dim_products` | quarantine (orphan) |

\*Out-of-observed-range but in-bounds → warn, keep; out of bounds → quarantine.

## 4. Rule engine design (reusable)

Rules are declarative metadata, not hand-written `if`s — so they're testable and easy to
extend. Sketch (non-binding):

```python
# validation.py
Rule = namedtuple("Rule", "name column predicate reason severity")  # predicate: Column -> BooleanColumn

PRODUCT_RULES = [
  Rule("P1_pk_not_null", "product_id", lambda c: c.isNotNull() & (c > 0), "null/invalid PK", "QUARANTINE"),
  # …
]

def apply_rules(df, rules):
    cond = F.lit(True)
    reasons = F.array()
    for r in rules:
        ok = r.predicate(F.col(r.column))
        reasons = F.when(~ok, F.array_union(reasons, F.array(F.lit(r.reason)))).otherwise(reasons)
        cond = cond & ok
    valid   = df.filter(cond)
    rejected = df.filter(~cond).withColumn("_reject_reasons", reasons)
    return valid, rejected
```

Referential integrity (L4) is a left-anti / left-semi join against the current Delta
dimension/fact (broadcast for the small `dim_products`).

## 5. Rejected-record handling ("Log rejected records")

Quarantined rows are written to:
`s3://ecom-lakehouse-quarantine-{env}/<dataset>/batch_id=<batch_id>/` as Parquet, with:
`_reject_reasons` (array<string>), `_batch_id`, `_source_file`, `_ingest_ts`, and the
original columns. A `quarantine_<dataset>` Athena table makes rejects queryable for
triage. Per-batch reject counts + top reasons are emitted as CloudWatch metrics and
written to the ledger row.

## 6. Data-quality gates & metrics
- `reject_rate = rejected / total`; **fail run if > 5%** (configurable per dataset).
- Emit metrics: `rows_in`, `rows_valid`, `rows_rejected`, `reject_rate`,
  `dedup_collapsed`, `orphans_fk` — per dataset per batch (`monitoring_observability.md`).
- Alert on: any structural failure, reject_rate breach, unknown department, FK orphan
  spike.

## 7. Validation vs. schema enforcement
Delta's schema enforcement (write-time) is the **last line of defense** — by the time we
MERGE, data already conforms because L1/L2 ran. If Delta still rejects a write, that's a
bug/contract drift → run fails loudly (never auto-evolve silently; see
`delta_lake_design.md`).

## 8. Acceptance criteria
- Dirty fixtures (null PK, bad timestamp, orphan FK, dup key, out-of-set department) are
  each routed to quarantine with the correct reason.
- Valid rows still load when a few rows are bad.
- Reject rate over threshold fails the run with a clear alert.
