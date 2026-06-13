.PHONY: fmt lint test test-unit test-integration plan apply destroy package clean

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

plan:
	cd infra/envs/dev && AWS_PROFILE=personal terraform plan -var-file=dev.tfvars

apply:
	cd infra/envs/dev && AWS_PROFILE=personal terraform apply -var-file=dev.tfvars -auto-approve

destroy:
	cd infra/envs/dev && AWS_PROFILE=personal terraform destroy -var-file=dev.tfvars -var="protect_stateful=false" -auto-approve

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
