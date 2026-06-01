# CI/CD — GitHub Actions

> Cites `testing_strategy.md`, `terraform.md`, `security_iam.md`. Owns Objective **O7** +
> US-7. ADR-013 (OIDC, no static keys). Brief: "Triggers must be scoped to the main branch."

## 1. Pipelines

| workflow | trigger | does | gate |
|----------|---------|------|------|
| `ci.yml` | PR + push to any branch | lint, type-check, unit + integration tests, `terraform fmt/validate/plan`, security scans | **required to merge** |
| `deploy.yml` | push to **`main`** only | build wheel → upload artifacts → `terraform apply` → deploy SF/Glue → smoke test | protected env approval for prod |

`deploy.yml` triggers are scoped exactly to `main` (brief requirement):
```yaml
on:
  push:
    branches: [main]
```

## 2. `ci.yml` stages (the brief's "CI of Spark job", "unit/integration tests")
1. **Setup** — checkout, Python, cache deps.
2. **Lint/format** — `black --check`, `isort --check`, `flake8`, `tflint`,
   `terraform fmt -check`.
3. **Security** — `detect-secrets`, `tfsec`/`checkov` (terrashark "compliance gate").
4. **Unit tests** — `pytest` + `chispa` on `lakehouse` library (local Spark + delta-spark;
   no AWS). Coverage threshold gate.
5. **Integration test** — end-to-end on sample + dirty fixtures against **local** Delta
   (a `tmp` warehouse), asserting validation/dedup/MERGE/idempotency
   (`testing_strategy.md`).
6. **Terraform plan** — `terraform validate` + `plan` (no apply) on `dev` for visibility.

## 3. `deploy.yml` stages
1. **Auth** — assume AWS role via **GitHub OIDC** (`aws-actions/configure-aws-credentials`,
   no long-lived keys — ADR-013).
2. **Package** — build the `lakehouse` wheel; upload Glue scripts + wheel + ASL template
   to the artifacts bucket (immutable, version-tagged by commit SHA).
3. **Infra** — `terraform apply` (dev auto; **prod gated by GitHub Environments manual
   approval**).
4. **Deploy definitions** — render + register the Step Functions ASL JSON and update Glue
   job script pointers (satisfies "Deploy Step Function definition as JSON/YAML").
5. **Smoke test** — trigger a Step Functions execution on a tiny canary file; assert it
   reaches `Succeed` and Athena returns rows; fail the deploy + alert on regression.
6. **Tag** — git tag the released SHA for traceability/rollback.

## 4. Environments & promotion
- `dev` deploys automatically on `main`.
- `prod` uses a **GitHub Environment** with required reviewers + wait timer → manual
  approval, matching `terraform.md` blast-radius guardrail.

## 5. Secrets & permissions
- **No `AWS_ACCESS_KEY_ID` secrets.** OIDC federation only; the deploy role trusts the
  repo+branch (`token.actions.githubusercontent.com`, `sub` scoped to `main`).
- Workflow permissions: `id-token: write`, `contents: read` (least privilege).
- Branch protection on `main`: require `ci.yml` green + PR review + linear history.

## 6. Caching & speed
- Cache pip + Spark/delta jars; matrix only if multiple Python versions are needed.
- Keep integration tests on small fixtures so CI stays fast (< ~10 min).

## 7. Rollback hook
On smoke-test failure, `deploy.yml` stops before flipping prod and alerts; manual rollback
procedure (re-deploy previous tagged artifacts + Delta time-travel restore) is in
`production_deployment.md`.

## 8. Acceptance criteria
- PRs cannot merge without green lint + tests + security scans.
- `deploy.yml` runs only on `main`, authenticates via OIDC, and deploys infra + SF/Glue.
- A failing smoke test blocks promotion and alerts.
- Released artifacts are immutable and tagged by commit SHA.
