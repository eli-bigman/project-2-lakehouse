# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION / VARIABLES
# ─────────────────────────────────────────────────────────────────────────────
PS          := powershell.exe -NoProfile -ExecutionPolicy Bypass -File
ENV         ?= dev

.PHONY: help clean deploy seed-data query query-sample ledger watermarks ui test test-unit test-integration fmt lint

# ─────────────────────────────────────────────────────────────────────────────
# HELP COMMAND
# ─────────────────────────────────────────────────────────────────────────────
help:
	@$(PS) scripts\run_with_env.ps1 scripts\help.ps1

# ─────────────────────────────────────────────────────────────────────────────
# LIFECYCLE
# ─────────────────────────────────────────────────────────────────────────────
clean:
	@$(PS) scripts\run_with_env.ps1 scripts\clean.ps1

deploy:
	@$(PS) scripts\run_with_env.ps1 scripts\deploy.ps1

# ─────────────────────────────────────────────────────────────────────────────
# DATA & INGESTION
# ─────────────────────────────────────────────────────────────────────────────
seed-data:
	@$(PS) scripts\run_with_env.ps1 python scripts\upload_sample_data.py

query:
	@$(PS) scripts\run_with_env.ps1 python scripts\query_athena.py

query-sample:
	@$(PS) scripts\run_with_env.ps1 python scripts\query_athena.py products-sample
	@$(PS) scripts\run_with_env.ps1 python scripts\query_athena.py orders-sample
	@$(PS) scripts\run_with_env.ps1 python scripts\query_athena.py order-items-sample

ledger:
	@$(PS) scripts\run_with_env.ps1 aws dynamodb scan --table-name ecom_lakehouse_ingestion_ledger_$(ENV) --query "Items[*].{file_key: file_key.S, status: status.S, raw_count: raw_count.N, clean_count: clean_count.N, reject_count: reject_count.N}" --output table

watermarks:
	@$(PS) scripts\run_with_env.ps1 aws dynamodb scan --table-name ecom_lakehouse_watermarks_$(ENV) --output table

# ─────────────────────────────────────────────────────────────────────────────
# DEVELOPMENT & TEST
# ─────────────────────────────────────────────────────────────────────────────
ui:
	@$(PS) scripts\run_with_env.ps1 streamlit run src/ui/app.py

test:
	@$(PS) scripts\run_with_env.ps1 pytest tests/

test-unit:
	@$(PS) scripts\run_with_env.ps1 pytest tests/unit/ -v

test-integration:
	@$(PS) scripts\run_with_env.ps1 pytest tests/integration/ -v

fmt:
	@$(PS) scripts\run_with_env.ps1 black src/ tests/
	@$(PS) scripts\run_with_env.ps1 isort src/ tests/

lint:
	@$(PS) scripts\run_with_env.ps1 flake8 src/ tests/
	@$(PS) scripts\run_with_env.ps1 black --check src/ tests/
	@$(PS) scripts\run_with_env.ps1 isort --check src/ tests/
