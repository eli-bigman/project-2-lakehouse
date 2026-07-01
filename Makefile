# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION / VARIABLES (Override via CLI, e.g. make demo-up ENV=prod)
# ─────────────────────────────────────────────────────────────────────────────
AWS_PROFILE    ?= sandbox-lakehouse-dev
AWS_REGION     ?= eu-west-1
ENV            ?= dev
TIMEOUT        ?= 900
POLL_INTERVAL  ?= 20

# Export for sub-processes (native cross-platform environment variables)
export AWS_PROFILE
export AWS_REGION
export ENV

DEMO_POLL_TIMEOUT_S  = $(TIMEOUT)
DEMO_POLL_INTERVAL_S = $(POLL_INTERVAL)
export DEMO_POLL_TIMEOUT_S
export DEMO_POLL_INTERVAL_S

.PHONY: fmt lint test test-unit test-integration plan apply destroy package clean \
        demo-up demo-down query query-sample ledger watermarks help

# ─────────────────────────────────────────────────────────────────────────────
# HELP COMMAND
# ─────────────────────────────────────────────────────────────────────────────
help:
	@echo "====================================================================="
	@echo " E-COMMERCE LAKEHOUSE ARCHITECTURE -- DEMO CLI"
	@echo "====================================================================="
	@echo "Available commands:"
	@echo "  make demo-up             - Provision infra, deploy Lambdas/Glue, ingest data"
	@echo "  make demo-down           - Empty S3 buckets and destroy all infrastructure"
	@echo "  make query               - Query Athena database for row counts"
	@echo "  make query-sample        - Fetch 5 sample records from Athena tables"
	@echo "  make ledger              - Scan DynamoDB Ingestion Ledger metadata"
	@echo "  make watermarks          - Scan DynamoDB Watermarks metadata"
	@echo "  make fmt / lint / test   - Format, lint, or run test suites"
	@echo "  make package             - Compile Spark wheel and stage files locally"
	@echo ""
	@echo "Parameters (override via command line, e.g., make demo-up ENV=prod):"
	@echo "  AWS_PROFILE   (Current: $(AWS_PROFILE))"
	@echo "  AWS_REGION    (Current: $(AWS_REGION))"
	@echo "  ENV           (Current: $(ENV))"
	@echo "  TIMEOUT       (Current: $(TIMEOUT)s)"
	@echo "  POLL_INTERVAL (Current: $(POLL_INTERVAL)s)"
	@echo "====================================================================="

# ─────────────────────────────────────────────────────────────────────────────
# DEMO ORCHESTRATION (ONE-COMMAND DEMO SUITE)
# ─────────────────────────────────────────────────────────────────────────────
demo-up:
	python scripts/demo_spinup.py

demo-down:
	python scripts/demo_teardown.py

# ─────────────────────────────────────────────────────────────────────────────
# ATHENA & DYNAMODB INSPECTION
# ─────────────────────────────────────────────────────────────────────────────
query:
	python scripts/query_athena.py

query-sample:
	python scripts/query_athena.py products-sample
	python scripts/query_athena.py orders-sample
	python scripts/query_athena.py order-items-sample

ledger:
	aws dynamodb scan --table-name ecom_lakehouse_ingestion_ledger_$(ENV) \
		--query "Items[*].{file_key: file_key.S, status: status.S, raw_count: raw_count.N, clean_count: clean_count.N, reject_count: reject_count.N}" \
		--output table

watermarks:
	aws dynamodb scan --table-name ecom_lakehouse_watermarks_$(ENV) --output table

# ─────────────────────────────────────────────────────────────────────────────
# CODE QUALITY & TESTING
# ─────────────────────────────────────────────────────────────────────────────
fmt:
	black src/ tests/
	isort src/ tests/

lint:
	flake8 src/ tests/
	black --check src/ tests/
	isort --check src/ tests/

test:
	pytest tests/

test-unit:
	pytest tests/unit/ -v

test-integration:
	pytest tests/integration/ -v

# ─────────────────────────────────────────────────────────────────────────────
# TERRAFORM MANUAL ACCESSORS
# ─────────────────────────────────────────────────────────────────────────────
plan:
	cd infra/envs/$(ENV) && terraform plan -var-file=$(ENV).tfvars

apply:
	cd infra/envs/$(ENV) && terraform apply -var-file=$(ENV).tfvars -auto-approve

destroy:
	cd infra/envs/$(ENV) && terraform destroy -var-file=$(ENV).tfvars -var="protect_stateful=false" -auto-approve

# ─────────────────────────────────────────────────────────────────────────────
# LOCAL PACKAGING
# ─────────────────────────────────────────────────────────────────────────────
package:
	pip wheel --no-deps -w dist/ .
	mkdir -p infra/files
	cp dist/*.whl infra/files/lakehouse-latest.whl
	cp src/glue_jobs/ingest.py infra/files/ingest.py
	cp src/glue_jobs/optimize.py infra/files/optimize.py

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf dist/ build/ .pytest_cache/
	find . -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
