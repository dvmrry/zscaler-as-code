PYTHON ?= python3
TF     ?= terraform

.PHONY: help env test test-floor validate schemas generate update-goldens

help: ## List available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

env: ## Print toolchain versions (diagnostic)
	@uname -sm
	@$(PYTHON) --version 2>&1 || echo "python3: not found"
	@$(MAKE) --version 2>/dev/null | head -1
	@$(TF) version 2>/dev/null | head -1 || echo "terraform: not found"
	@docker --version 2>/dev/null || echo "docker: not found"

test: ## Run Python unit tests with the local interpreter
	$(PYTHON) -m unittest discover -s tools/tests -t . -v

test-floor: ## Run unit tests under Python 3.6 in Docker (optional dev check; needs docker)
	docker run --rm -v "$$(pwd)":/repo -w /repo python:3.6.8-slim \
		python -m unittest discover -s tools/tests -t . -v

validate: ## Terraform formatting checks
	$(TF) fmt -check -recursive

schemas: ## Re-extract provider schemas into schemas/provider/ (CHECK=1 fails on drift)
	$(TF) -chdir=tools/schema-extract init -input=false
	@echo "resolved provider versions:"
	@grep -E '^provider|^  version' tools/schema-extract/.terraform.lock.hcl
	$(TF) -chdir=tools/schema-extract providers schema -json | $(PYTHON) tools/extract_schemas.py
ifeq ($(CHECK),1)
	@git diff --exit-code --stat -- schemas/provider || { \
		echo ""; \
		echo "schemas/provider/ drifted from the committed dumps."; \
		echo "Compare the resolved versions above with the pins in"; \
		echo "tools/schema-extract/main.tf — version drift is the usual cause."; \
		echo "Never hand-edit schemas/provider/; fix the pins and regenerate."; \
		exit 1; }
endif

generate: ## Generate modules + tfvars schemas from provider dumps (CHECK=1 fails on drift)
	$(PYTHON) -m tools.gen_module
ifeq ($(CHECK),1)
	@git diff --exit-code --stat -- modules schemas/tfvars || { \
		echo ""; \
		echo "Generated output drifted from what is committed."; \
		echo "Never hand-edit modules/ — fix the generator or an override,"; \
		echo "run 'make generate', and commit the result."; \
		exit 1; }
endif

update-goldens: ## Re-bless generator golden fixtures from current output
	$(PYTHON) -m tools.gen_module
	rm -rf tools/tests/fixtures/gen
	mkdir -p tools/tests/fixtures/gen/zpa_segment_group
	cp modules/zpa_segment_group/variables.tf modules/zpa_segment_group/main.tf \
		modules/zpa_segment_group/outputs.tf modules/zpa_segment_group/versions.tf \
		tools/tests/fixtures/gen/zpa_segment_group/
	mkdir -p tools/tests/fixtures/gen/zia_url_categories
	$(PYTHON) -c "from tools.tfschema import load_resource; from tools.gen_module import render_variables, render_main, render_outputs, render_versions, _fmt; rt = 'zia_url_categories'; rs = load_resource(rt); base = 'tools/tests/fixtures/gen/zia_url_categories/'; open(base+'variables.tf','w').write(_fmt(render_variables(rt, rs))); open(base+'main.tf','w').write(_fmt(render_main(rt, rs))); open(base+'outputs.tf','w').write(_fmt(render_outputs(rt, rs))); open(base+'versions.tf','w').write(_fmt(render_versions(rt, rs)))"
