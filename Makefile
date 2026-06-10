PYTHON ?= python3
TF     ?= terraform

.PHONY: help test test-floor validate schemas

help: ## List available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

test: ## Run Python unit tests with the local interpreter
	$(PYTHON) -m unittest discover -s tools/tests -t . -v

test-floor: ## Run unit tests under Python 3.6 in Docker (optional dev check; needs docker)
	docker run --rm -v "$$(pwd)":/repo -w /repo python:3.6.8-slim \
		python -m unittest discover -s tools/tests -t . -v

validate: ## Terraform formatting checks
	$(TF) fmt -check -recursive

schemas: ## Re-extract provider schemas into schemas/provider/
	$(TF) -chdir=tools/schema-extract init -input=false
	$(TF) -chdir=tools/schema-extract providers schema -json | $(PYTHON) tools/extract_schemas.py
