# Decision Log (Architecture Decision Records)

> Senior-engineer decisions for the Lakehouse project. Each ADR records **context →
> decision → rationale → trade-offs / rejected alternatives → consequences.** This log
> is authoritative for *why*; `architecture.md` is authoritative for *what*.

| ADR | Title | Status |
|-----|-------|--------|
| ADR-001 | All deliverables live in `docs/` | Accepted |
| ADR-002 | Normalize `.xlsx`/`.csv` → Parquet before Spark | Accepted |
| ADR-003 | Delta Lake on S3 as the table format | Accepted |
| ADR-004 | Medallion zoning (raw/staging/dwh/archive/quarantine) | Accepted |
| ADR-005 | ~~Partition facts by `order_date`~~ → unpartitioned + Z-Order (volume-based promotion) | **Revised (review 1.2)** |
| ADR-006 | DynamoDB control plane — ledger + watermarks (~~schema registry~~ removed) | **Revised (review 1.5)** |
| ADR-007 | Idempotency via ledger + Delta MERGE (exactly-once effect) | Accepted |
| ADR-008 | Reject-and-quarantine over fail-fast for bad records | Accepted |
| ADR-009 | Region `us-east-1`, two envs (dev/prod) | Accepted (assumption) |
| ADR-010 | Step Functions Standard (not Express) | Accepted |
| ADR-011 | Glue Spark for ETL (per-dataset); **Lambda** for normalization | **Revised (review 1.1/1.4)** |
| ADR-012 | Terraform as the sole IaC tool; remote state | Accepted |
| ADR-013 | GitHub Actions with OIDC (no long-lived AWS keys) | Accepted |
| ADR-014 | Surrogate dedup via `_record_hash`; merge on natural keys | Accepted |
| ADR-015 | Athena native Delta (Glue Crawler optional; no symlink/`MSCK`) | **Revised (review 1.3)** |
| ADR-016 | No Glacier transitions for small files (S3 Standard / Intelligent-Tiering) | Accepted (review 2.1) |
| ADR-017 | KMS CMKs **with S3 Bucket Keys** (AWS-managed allowed in dev) | Accepted (review 2.2) |
| ADR-018 | Explicit resource blocks + `prevent_destroy` for stateful buckets | Accepted (review 2.3) |
| ADR-019 | DynamicFrames prohibited for Delta; Spark DataFrames only | Accepted (review R2-1.1) |
| ADR-020 | Glue Spark: fixed 2-worker minimum (`G.1X`), auto-scaling disabled | Accepted (review R2-1.1) |
| ADR-021 | S3 bucket policy: enforce KMS encryption at upload (block SSE-S3 override) | Accepted (review R2-3.1) |

---

## Review Disposition — `.ai/review.md` (Senior AWS/DE/Terraform Review, May 2026)

The review was evaluated item-by-item against the brief's **mandatory** constraints
(S3, **Glue + Spark**, Delta Lake, Step Functions, Glue Catalog, Athena, GitHub Actions)
and the **profiled data scale** (~500 orders / 2,768 items / 1,000 products per monthly
drop). Recommendations were **not** accepted automatically. Verdicts:

| # | Recommendation | Verdict | Reason |
|---|----------------|---------|--------|
| 1.1 | Lambda instead of Glue Python-shell for normalization | **ACCEPT** | Lambda: ms start + ms billing, pandas/openpyxl via layer/container; ~95% cheaper for ~1 MB files. Brief doesn't constrain the normalizer's runtime, and we already use Lambda for claim/archive. → ADR-011 revised. |
| 1.2 | Unpartitioned + Z-Order instead of daily `order_date` partitioning | **ACCEPT (reframed)** | Conclusion correct: physical partitioning fragments tiny data. *Note:* the reviewer's "16 rows/partition" math is wrong — all orders in a monthly file share one `order_date`, so daily partitioning yields ~12 partitions/yr each holding a month, not 16 rows. We keep partitioning as a **documented, justified decision with a volume-based promotion threshold** (the brief lists "Partitioning for performance" + "justify your partitioning logic" as deliverables — we satisfy them by demonstrating judgment, not by removing the topic). → ADR-005 revised. |
| 1.3 | Drop symlink manifests / `MSCK`; use native Athena v3 Delta | **ACCEPT** | We already pin Athena v3; native `table_type=DELTA` reads the transaction log directly. Removes S3 manifest writes + catalog repairs. → ADR-015 revised, `catalog_and_athena.md`. |
| 1.4a | Consolidate 3 Spark jobs into 1 multi-dataset session | **REJECT as default / accept as documented option** | The brief's orchestration requirement explicitly says *"Run a Glue Job (with Delta Lake) **for each dataset**."* Our per-dataset Glue tasks already satisfy this **and** preserve per-dataset retry/branching granularity (also required). Consolidation contradicts the literal requirement for negligible savings at monthly cadence. Captured as an *optional* optimization with its trade-off, not the default. |
| 1.4b | Replace Spark with Rust `deltalake` in Lambda | **REJECT** | **Directly violates a mandatory constraint:** "AWS Glue + Spark — Distributed ETL jobs" and "Write modular, reusable Spark code." A cost optimization cannot override a stated requirement and learning objective. |
| 1.5 | Remove DynamoDB schema registry; rely on Delta + in-code schema | **ACCEPT** | Delta enforces schema on write; the normalizer already validates against the in-code Design-Contract schema (`schemas.py`). The registry's only extra value (versioning) is moot because we gate schema changes through code review (no auto-evolve). Removing it cuts a state table + IAM surface. → ADR-006 revised; `dynamodb_schema.md`, `data_handling.md`, `data_validation.md`, `security_iam.md`. |
| 2.1 | No Glacier transitions for small files | **ACCEPT** | Glacier per-object overhead + transition request fees exceed storage savings for ~1 MB monthly files. Use S3 Standard (Intelligent-Tiering optional). → ADR-016, `data_handling.md`, `architecture.md` §3.2, `terraform.md`. |
| 2.2 | AWS-managed keys, or CMKs with S3 Bucket Keys | **PARTIAL** | **Accept fully:** enable S3 Bucket Keys (~99% KMS API cost cut, no downside). **Decline (prod):** keep **CMKs** as prod default — key policies enforce least-privilege/rotation/audit for a "production-grade" system (our security posture). **Accept (dev):** AWS-managed keys allowed in dev to cut fixed fees. Net = CMK + Bucket Keys. → ADR-017, `security_iam.md`, `terraform.md`. |
| 2.3 | Explicit resource blocks + `prevent_destroy` for stateful buckets | **ACCEPT** | Real Terraform footgun (renaming a `for_each` key destroys the resource = identity churn, a terrashark guardrail we already cite). Declare stateful buckets (`raw`,`dwh`,`archive`,`quarantine`) explicitly with `prevent_destroy`; keep `for_each` for stateless zones (`staging`,`athena-results`,`artifacts`). `prevent_destroy` was already planned. → ADR-018, `terraform.md`. |
| §4 | Verification plan (small-file query, Lambda vs Glue benchmark, accidental-destroy test) | **ACCEPT** | Sound empirical checks; folded into `testing_strategy.md` as implementation-phase verification tasks. |

**Summary:** 7 accepts (1.1, 1.2, 1.3, 1.5, 2.1, 2.3, §4), 1 partial (2.2), 2 rejects
(1.4a default, 1.4b) — the two rejects both protect mandatory brief compliance (Glue +
Spark, "a Glue Job for each dataset"). The review's cost analysis is generally strong;
the rejects are where cost optimization collided with explicit requirements.

---

## ADR-001 — All deliverables live in `docs/`
**Context.** The brief states: *"The only deliverables are the .md files stored in the
docs/ directory."* Common convention would put `master_plan.md` at repo root.
**Decision.** Place **every** planning document — including `master_plan.md` and
`decision.md` — inside `docs/`. Files keep their named filenames.
**Rationale.** The explicit written constraint dominates convention; honoring it costs
nothing and `docs/master_plan.md` is still trivially the entry point.
**Trade-off.** Slightly less conventional than a root-level README/master plan. Accepted.

## ADR-002 — Normalize `.xlsx`/`.csv` → Parquet before Spark
**Context.** The brief says data is "ingested from CSVs," but the actual order &
order-items files are **`.xlsx`**. Spark/Glue **cannot read `.xlsx` natively** (no
built-in reader; `spark-excel` is a fragile third-party JAR not suited to production).
**Decision.** Add a deterministic **normalization step** in front of the Spark ETL: a
lightweight **Glue Python-shell job (or Lambda)** using `pandas`+`openpyxl` reads the
source file, applies the canonical column order/names, and writes **Parquet** to the
`staging` zone. Spark/Delta then reads Parquet. CSVs skip Excel parsing but still pass
through normalization for consistent typing.
**Rationale.** Keeps the heavy Spark job format-agnostic and deterministic; isolates the
brittle Excel dependency in a small, testable, cheap component; Parquet gives typed,
columnar, splittable input.
**Trade-offs / rejected.** (a) `spark-excel` JAR — rejected: version-coupled to Spark,
poor large-file behavior, hard to test. (b) Force upstream to send CSV — not in our
control for the provided sample; we still accept CSV natively. (c) Skip normalization —
impossible, Spark can't open `.xlsx`.
**Consequence.** `data_handling.md` and `glue_jobs.md` define a 2-job-per-dataset shape
(normalize → ingest), reflected in the Step Functions design.

## ADR-003 — Delta Lake on S3 as the table format
**Context.** Brief mandates ACID, schema enforcement, upserts, dedup on S3.
**Decision.** Use **Delta Lake** tables in the `dwh` zone.
**Rationale.** ACID transactions, `MERGE` (upsert), schema enforcement/evolution, time
travel, and `OPTIMIZE`/`VACUUM` — directly satisfy the deliverables. Glue 4.0+ ships
Delta support.
**Trade-offs / rejected.** Iceberg (also strong; team/tooling familiarity and brief
phrasing favor Delta) and Hudi (more operational overhead). Plain Parquet rejected — no
ACID/MERGE.

## ADR-004 — Medallion zoning
**Decision.** Separate buckets per zone: `raw`, `staging`, `dwh`, `archive`,
`quarantine`, plus `athena-results` and `artifacts` (see `architecture.md` §3.2).
**Rationale.** Blast-radius isolation, independent lifecycle/retention, clear IAM
boundaries, and a clean immutable-raw guarantee.
**Trade-off.** More buckets to manage — handled by Terraform modules.

## ADR-005 — Partitioning (REVISED — review 1.2)
**Original decision.** Partition `fct_orders` / `fct_order_items` by `order_date`.
**Revised decision.** Keep all three tables **unpartitioned** at current volume; use Delta
**Z-ORDER + data skipping** (`order_date` on facts, `department` on the dim) for query
performance. Define a **volume-based promotion threshold**: introduce physical
`order_date` partitioning only once a single date partition would hold a meaningful amount
of data (rule of thumb ≈ ≥1 GB, or sustained daily order volume — see
`delta_lake_design.md` §3).
**Rationale.** At ~500 orders / ~2,768 items per monthly drop, physical date partitioning
fragments the table into tiny KB-scale files → Delta-log + S3-GET overhead makes Athena/
Spark slower and costlier (the small-file problem). Z-Order gives the pruning benefit
without the fragmentation.
**Note on the review's premise.** The reviewer's "16 rows per partition" figure is
inaccurate — every order in a monthly file shares one `order_date`, so daily partitioning
would yield ~12 partitions/year each holding a *month*, not 16 rows. The **conclusion**
(don't physically partition this volume) is nonetheless correct, so we adopt it.
**Brief alignment.** "Partitioning for performance" + "justify your partitioning logic"
are satisfied by a *documented, threshold-gated* partitioning strategy that demonstrates
judgment — stronger than reflexively partitioning tiny data.
**Trade-off.** A future cutover to physical partitioning requires a one-time rewrite/
backfill of the table; acceptable and documented.

## ADR-006 — Introduce DynamoDB control plane
**Context.** DynamoDB is **not** in the brief's core-services list; it appears only in
the user's addendum ("DynamoDB schema design").
**Decision (REVISED — review 1.5).** Add a small DynamoDB control plane: **ingestion
ledger** (idempotency) + **watermarks**. The previously-planned **schema registry table
is removed.**
**Rationale.** S3 alone cannot give a cheap, strongly-consistent, conditional-write
record of "have we already processed this file?" DynamoDB conditional writes provide
exactly-once *triggering* and clean re-run semantics; it's serverless and pennies at this
scale.
**Why drop the schema registry.** Delta Lake already enforces schema on write, and the
normalizer validates incoming structure against the **in-code schema**
(`src/lakehouse/schemas.py`, mirroring the Design Contract) before Spark. The registry's
only marginal value was *versioning* expected schemas without a deploy — but we gate all
schema changes through code review + migration (no auto-evolve, `delta_lake_design.md`
§4), so that value is moot. Removing it deletes a state table, custom code, and IAM grants.
**Trade-offs / rejected.** (a) S3 manifest for idempotency — weaker consistency, race
conditions. (b) Delta MERGE alone — row-level idempotency only, can't "skip
already-archived file" at orchestration time. We use ledger **+** MERGE (ADR-007).
**Consequence.** Two DynamoDB tables (ledger, watermarks); least-privilege IAM in
`security_iam.md`; details in `dynamodb_schema.md`.

## ADR-007 — Idempotency via ledger + MERGE
**Decision.** Two complementary layers: (1) **DynamoDB ledger** conditional-write guards
whether a file is processed/archived; (2) **Delta MERGE** on natural keys makes row
writes idempotent even if a job re-runs mid-flight.
**Rationale.** Defense in depth → exactly-once *effect* end to end (US-2).
**Trade-off.** Slight extra complexity vs. a single mechanism; worth it for reliability.

## ADR-008 — Reject-and-quarantine, not fail-fast
**Decision.** Invalid records are **filtered to a quarantine zone** with a reason code;
the valid subset still loads. A run fails only if reject rate exceeds a threshold
(e.g. >5%, configurable) or a structural/schema error occurs.
**Rationale.** Maximizes freshness/availability while preserving auditability; one bad
row shouldn't block a whole monthly load.
**Trade-off.** Partial loads possible — surfaced via metrics + the ledger row counts so
they're never silent.

## ADR-009 — Region & environments
**Decision.** `us-east-1`; two environments `dev` and `prod` (env suffix on resources).
**Rationale.** Default low-cost region with full service availability; dev/prod parity
via the same Terraform modules and different `tfvars`.
**Status.** Assumption — revisit if data residency requirements emerge.

## ADR-010 — Step Functions Standard
**Decision.** Use **Standard** workflows.
**Rationale.** Long-running (Glue jobs minutes-long), exactly-once, full execution
history, native Glue/Lambda/SNS integrations and retry/catch. Express is for
high-volume short events — wrong fit.
**Trade-off.** Per-transition pricing > Express, but volume is tiny (monthly).

## ADR-011 — Compute split (REVISED — review 1.1 / 1.4)
**Decision.** **Glue Spark** for validation/transform/Delta MERGE, invoked **per dataset**
(one parameterized job; one Step Functions task per dataset). **AWS Lambda** (pandas/
openpyxl via a layer or container image) for cheap xlsx/csv→Parquet normalization.
**Rationale.** Match compute to the task: don't spin a Spark cluster to parse a ~1.6 MB
spreadsheet (normalization → Lambda: ms cold start, ms billing, ~95% cheaper than a Glue
Python-shell DPU-minute). Keep the heavy, distributed work in **Glue + Spark** as the
brief mandates.
**Rejected — replace Spark with Rust `deltalake` in Lambda (review 1.4b).** Violates the
mandatory requirement "AWS Glue + Spark — Distributed ETL jobs" and "Write modular,
reusable Spark code." Cost cannot override a stated requirement.
**Rejected as default — consolidate all datasets into one Spark session (review 1.4a).**
The brief requires "Run a Glue Job (with Delta Lake) **for each dataset**," and per-dataset
jobs give cleaner per-dataset retry/branching. Single-session consolidation is recorded
as an *optional* cost optimization in `glue_jobs.md`, not the default.

## ADR-012 — Terraform as sole IaC
**Decision.** All infra in **Terraform**, **remote state** (S3 backend + DynamoDB lock),
modular per concern, env via workspaces/tfvars.
**Rationale.** Reproducibility (US-7), reviewable diffs, mature AWS provider. See
`terraform.md`; guardrails align with the `terrashark` skill's failure modes.
**Rejected.** CloudFormation/CDK/SAM — fine, but Terraform chosen for module ecosystem
and multi-account portability.

## ADR-013 — GitHub Actions with OIDC
**Decision.** CI/CD assumes an AWS role via **GitHub OIDC**; no static AWS keys in repo.
Deploy triggers scoped to `main`.
**Rationale.** Eliminates long-lived secrets (a `terrashark` "secret exposure" failure
mode); satisfies the brief's branch-scoping requirement.

## ADR-014 — Dedup strategy
**Decision.** Compute `_record_hash` = sha-256 over business columns; MERGE on the
natural key, and within a batch keep one row per key by latest `_ingest_ts`. Cross-file
dedup is handled because MERGE targets the same key in the existing table.
**Rationale.** Satisfies "deduplication across files" precisely and deterministically.

## ADR-015 — Athena native Delta; crawler optional (REVISED — review 1.3)
**Decision.** Register catalog tables explicitly as **native Delta** (`table_type=DELTA`)
so **Athena v3 reads the Delta transaction log directly**. **Drop** symlink manifest
generation (`_symlink_format_manifest`) and `MSCK REPAIR`. Glue Crawler remains optional
for `dev` discovery only.
**Rationale.** Native Delta in Athena v3 (which we pin) handles partition pruning and
schema directly from the transaction log — no manifest files (saves S3 writes + Glue
cycles) and no catalog repairs. Explicit managed DDL stays deterministic and
version-controlled; crawlers can mis-infer types and add latency/cost.
**Supersedes** the earlier symlink/`MSCK` fallback described before this review.

## ADR-016 — Storage tiering: no Glacier for small files (review 2.1)
**Decision.** Keep `raw` and `archive` in **S3 Standard** (or **Intelligent-Tiering** if
access becomes unpredictable). Do **not** transition these objects to Glacier.
**Rationale.** Glacier carries per-object metadata overhead + per-transition request fees
and minimum-duration charges. For ~1 MB monthly files, those costs exceed the storage
savings. Intelligent-Tiering is the safe middle ground if volume grows; Glacier only makes
sense for large (>~10 MB) cold objects, which we don't have.
**Trade-off.** Marginally higher per-GB storage than Glacier — negligible at this volume,
and offset by avoided request/overhead costs + instant retrieval.

## ADR-017 — KMS CMKs with S3 Bucket Keys (review 2.2)
**Decision.** **prod:** customer-managed KMS keys (CMKs) per data class **with S3 Bucket
Keys enabled** (`bucket_key_enabled = true`) and DynamoDB SSE-KMS. **dev:** AWS-managed
keys permitted to avoid fixed per-key fees.
**Rationale.** CMKs give key policies (least-privilege at the key), rotation, and
audit — appropriate for a "production-grade" system and consistent with our least-
privilege posture (`security_iam.md`). **S3 Bucket Keys** cut KMS API request charges by
up to ~99%, neutralizing the main cost objection. We therefore *keep* CMKs in prod rather
than downgrade to AWS-managed keys, while adopting the reviewer's Bucket Keys point in full.
**Trade-off.** ~$1/key/month fixed cost in prod — accepted for the security/audit value;
dev avoids it with AWS-managed keys.

## ADR-018 — Explicit stateful buckets + `prevent_destroy` (review 2.3)
**Decision.** Declare **stateful** buckets (`raw`, `dwh`, `archive`, `quarantine`) as
**explicit individual `resource` blocks** with `lifecycle { prevent_destroy = true }`.
Reserve `for_each` iteration for **stateless** zones (`staging`, `athena-results`,
`artifacts`) where destroy/recreate is safe.
**Rationale.** Renaming/removing a `for_each` map key forces Terraform to **destroy** that
resource — catastrophic for buckets holding warehouse data (this is the terrashark
"identity churn" / "blast radius" failure mode we already guard against in `terraform.md`
§5). Explicit blocks + `prevent_destroy` make accidental teardown impossible (apply errors
instead). `prevent_destroy` on stateful stores was already planned; this ADR also pins the
*declaration style*.
**Trade-off.** Slightly more verbose HCL for the stateful buckets — worth it for safety.

## ADR-019 — DynamicFrames prohibited for Delta; Spark DataFrames only (review R2-1.3)
**Context.** Glue's proprietary `DynamicFrame` API (`create_dynamic_frame.from_catalog`,
`GlueContext`, etc.) does **not** support Delta Lake. Using it silently bypasses the Delta
transaction log, causing undefined behavior or plain Parquet reads of stale state.
**Decision.** All Delta reads and writes in Glue jobs **must** use native Spark DataFrames:
`spark.read.format("delta").load(path)` and `df.write.format("delta").save(path)` (or
`DeltaTable.forPath`). `DynamicFrame` usage is prohibited in any job that touches Delta.
**Rationale.** Runtime blocker if violated: the Delta table appears to exist but reads/
writes silently bypass the transaction log. Documented AWS Glue 4.0 limitation.
**Consequence.** The code sketch in `glue_jobs.md` §4 already uses native Spark DataFrames
(`spark.read.*`, `df.write.*`) — this ADR makes it a hard constraint. CI lint rule or a
pre-commit check can grep for `create_dynamic_frame` in job files.

## ADR-020 — Glue Spark: fixed 2-worker minimum (`G.1X`), auto-scaling disabled (review R2-1.1)
**Context.** AWS Glue Spark batch jobs require a **hard minimum of 2 workers**: one driver +
one executor. Attempting 1 worker fails API validation. The `G.025X` worker type is
**streaming-only** (Glue 3.0+ Streaming ETL) and cannot be used for batch jobs. Auto-
scaling on short-running jobs (< 2–3 min) adds orchestration-analysis overhead that
*increases* wall-clock time.
**Decision.** For all `ingest_*` and `optimize_*` Glue Spark jobs: **worker type `G.1X`,
fixed `NumberOfWorkers = 2` (minimum), auto-scaling disabled**. Scale to `G.2X` or more
workers only if profiling shows a bottleneck.
**Rationale.** Avoids API validation failures at deploy time; avoids auto-scaling overhead
for sub-2-minute batch jobs on MB-scale data; `G.1X` at 2 workers is the smallest valid
configuration.
**Trade-off.** Slightly over-provisioned for ~500-row batches; at a 1-minute billing
minimum and ~monthly cadence the cost is negligible.

## ADR-021 — S3 bucket policy: enforce KMS encryption at upload (review R2-3.1)
**Context.** S3 default (bucket-level) encryption is a *fallback* — if a client upload
explicitly sets `x-amz-server-side-encryption: AES256` (SSE-S3), that header overrides the
bucket default and the object lands without KMS encryption. This silently undermines our
KMS-at-rest security posture.
**Decision.** Add an explicit `Deny` statement to each data bucket policy that blocks any
`s3:PutObject` where `s3:x-amz-server-side-encryption` is **not** `aws:kms`. This policy
is **in addition to** the existing `EnforceHTTPS` Deny statement (block non-TLS transport).
Together they enforce: (1) all traffic uses HTTPS; (2) all at-rest encryption uses KMS.
**Rationale.** Closes the SSE-S3 override gap; required for compliance postures that mandate
CMK-level key management and audit (Security Hub S3.5 equivalent).
**Consequence.** Any client that uploads without explicitly specifying `aws:kms` (e.g.
a mis-configured S3 CLI call) will receive a `403 Access Denied` — fast, clear failure
rather than silent encryption downgrade. Terraform sketch updated in `terraform.md`.
**Trade-off.** Slightly more restrictive; upload clients must explicitly set the correct
header. Glue/Lambda IAM roles use the AWS SDK which respects bucket defaults — as long as
they don't override the header, they'll pass. Adds one `Deny` statement per bucket policy.

---

## Review Disposition — Round 2 `.ai/review.md` (Validated AWS/DE/Terraform Review, May 2026)

Items evaluated against mandatory brief constraints and previously-accepted ADRs.

| # | Recommendation | Verdict | Reason |
|---|----------------|---------|--------|
| R2-1.1 (worker min) | Fixed 2-worker minimum + G.025X streaming-only warning | **ACCEPT** | Documented AWS API constraint: 1-worker Spark jobs fail validation; G.025X is streaming-only. → ADR-020. `glue_jobs.md` updated. |
| R2-1.1 (auto-scale) | Disable auto-scaling for short batch jobs | **ALREADY IN DOCS** | `glue_jobs.md` §8 already states "auto-scaling off." Moved to config table (§2) and made explicit. → ADR-020. |
| R2-1.1 (DynamicFrame) | DynamicFrames incompatible with Delta; Spark DataFrames only | **ACCEPT** | Real execution blocker not previously documented. Every Delta read/write must use native Spark DataFrames. → ADR-019. `glue_jobs.md`, `transformation_logic.md` updated. |
| R2-1.1 (Lake Formation) | VACUUM/OPTIMIZE must use Spark APIs if Lake Formation is enabled | **ACCEPT** | Valid operational constraint. Lake Formation governance tables restrict catalog interfaces. Noted as a constraint in `glue_jobs.md` §7. |
| R2-1.2 | Unpartitioned + Z-Order (1 TB rule) | **ALREADY ADOPTED** | Accepted in round 1 as ADR-005 (revised). No change. |
| R2-1.3 (symlink/MSCK) | Eliminate symlink manifests and MSCK REPAIR | **ALREADY ADOPTED** | Accepted in round 1 as ADR-015. No change. |
| R2-1.4 | Sparkless `delta-rs` in Lambda (full pipeline) | **REJECT** | Same grounds as round 1 (1.4b): mandatory constraint "AWS Glue + Spark — Distributed ETL jobs" and "Write modular, reusable Spark code." Repeated in both reviews. Rationale stands. |
| R2-2.1 | Standard Workflows validation (cost/guarantee analysis) | **CONFIRMED** | Validates ADR-010. The 5-min Express timeout + exactly-once semantics were already our justification. The cost analysis (~$0.15/month) confirms Standard is also the cheaper choice at this volume once CloudWatch logging for Express is factored in. No doc change needed beyond this note. |
| R2-3.1 | EnforceKMSEncryption Deny policy (block SSE-S3 override) | **ACCEPT** | Real security gap: S3 default encryption is a fallback that a client header can override. The `EnforceKMSEncryption` Deny closes the gap. New, not previously documented. → ADR-021. `security_iam.md`, `terraform.md` updated. |
| R2-3.2 | Concrete Terraform HCL (validated KMS + policy pattern) | **ACCEPT** | Replaces our earlier illustrative sketch with a validated, functionally correct pattern. → `terraform.md` sketch updated. |
| R2-3.3 | Glacier minimum size / KMS Bucket Keys | **ALREADY ADOPTED** | ADR-016 (no Glacier) and ADR-017 (Bucket Keys). No change. |
| R2-3.4 | Dynamic `for_each` + `prevent_destroy` | **ALREADY ADOPTED** | ADR-018. No change. |

**Summary:** 3 new accepts (R2-1.1 worker/DynamicFrame/LakeFormation, R2-3.1 KMS policy, R2-3.2 HCL); 1 confirmed (R2-2.1); 4 already adopted (R2-1.2, R2-1.3, R2-3.3, R2-3.4); 1 reject (R2-1.4 — same standing rejection, brief compliance).

---

## Open questions / assumptions to confirm with stakeholders
1. **Drop cadence & SLA** — assumed monthly; confirm freshness SLA (drives trigger mode).
2. **Trigger mechanism** — S3 EventBridge notification vs scheduled poll (we plan event-
   driven, with a scheduled fallback). Confirm whether real S3 events are available or we
   "simulate" per the brief.
3. **PII** — `user_id` treated as non-PII surrogate; confirm no direct identifiers arrive.
4. **Reject-rate threshold** — assumed 5%; confirm business tolerance.
5. **Region/residency** — assumed `us-east-1`; confirm.
6. **Late/out-of-order data & corrections** — assumed handled by MERGE upsert; confirm
   whether hard-deletes are ever required (would need CDC/tombstones).
7. **"agent stick-holding" / "telephone skill" terminology** — these idiosyncratic terms
   from the task brief are interpreted (assumption) as a *working style*: prescriptive,
   ordered, self-contained, verifiable steps (inputs → action → expected output →
   verification) that survive agent-to-agent hand-off without intent degrading (the
   "telephone game"). Applied in `terraform.md` §0 and `sprint_planning.md` §1/§6.
   Confirm this matches intent.
