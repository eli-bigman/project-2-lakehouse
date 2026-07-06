# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION / VARIABLES
# ─────────────────────────────────────────────────────────────────────────────
ifeq ($(OS),Windows_NT)
    PS          := powershell.exe -NoProfile -ExecutionPolicy Bypass -File
    RUN_ENV     := $(PS) scripts\run_with_env.ps1
    BIN         := .venv\\Scripts\\
    SEP         := \\
else
    RUN_ENV     := 
    BIN         := 
    SEP         := /
endif
ENV         ?= dev

.PHONY: help clean deploy seed-data query query-sample ledger watermarks ui test test-unit test-integration fmt lint

# ─────────────────────────────────────────────────────────────────────────────
# HELP COMMAND
# ─────────────────────────────────────────────────────────────────────────────
help:
	@$(RUN_ENV) scripts$(SEP)help.ps1

# ─────────────────────────────────────────────────────────────────────────────
# LIFECYCLE
# ─────────────────────────────────────────────────────────────────────────────
clean:
	@$(RUN_ENV) scripts$(SEP)clean.ps1

deploy:
	@$(RUN_ENV) scripts$(SEP)deploy.ps1

# ─────────────────────────────────────────────────────────────────────────────
# DATA & INGESTION
# ─────────────────────────────────────────────────────────────────────────────
seed-data:
	@$(RUN_ENV) python scripts$(SEP)upload_sample_data.py

query:
	@$(RUN_ENV) python scripts$(SEP)query_athena.py

query-sample:
	@$(RUN_ENV) python scripts$(SEP)query_athena.py products-sample
	@$(RUN_ENV) python scripts$(SEP)query_athena.py orders-sample
	@$(RUN_ENV) python scripts$(SEP)query_athena.py order-items-sample

ledger:
	@$(RUN_ENV) aws dynamodb scan --table-name ecom_lakehouse_ingestion_ledger_$(ENV) --query "Items[*].{file_key: file_key.S, status: status.S, rows_in: rows_in.N, rows_valid: rows_valid.N, rows_rejected: rows_rejected.N}" --output table

watermarks:
	@$(RUN_ENV) aws dynamodb scan --table-name ecom_lakehouse_watermarks_$(ENV) --output table

# ─────────────────────────────────────────────────────────────────────────────
# DEVELOPMENT & TEST
# ─────────────────────────────────────────────────────────────────────────────
ui:
	@$(RUN_ENV) streamlit run src/ui/app.py

test:
	@$(RUN_ENV) $(BIN)pytest tests/

test-unit:
	@$(RUN_ENV) $(BIN)pytest tests/unit/ -v

test-integration:
	@$(RUN_ENV) $(BIN)pytest tests/integration/ -v

fmt:
	@$(RUN_ENV) $(BIN)black src/ tests/
	@$(RUN_ENV) $(BIN)isort src/ tests/

lint:
	@$(RUN_ENV) $(BIN)flake8 src/ tests/
	@$(RUN_ENV) $(BIN)black --check src/ tests/
	@$(RUN_ENV) $(BIN)isort --check src/ tests/
