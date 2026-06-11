PYTHON ?= python3
TF     ?= terraform

.PHONY: help env test test-floor validate schemas generate gen-env transform fetch fetch-diag update-goldens update-demo-goldens test-modules test-envs validate-imports plan drift check-envs validate-config demo check-demo typecheck conformance

help: ## List available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'

env: ## Print toolchain versions (diagnostic)
	@uname -sm
	@$(PYTHON) --version 2>&1 || echo "python3: not found"
	@$(MAKE) --version 2>/dev/null | head -1
	@$(TF) version 2>/dev/null | head -1 || echo "terraform: not found"
	@docker --version 2>/dev/null || echo "docker: not found"

install-tf: ## Download+checksum-verify a pinned terraform (VERSION=<v> [DEST=bin]); then PATH it or pass TF=bin/terraform
	@test -n "$(VERSION)" || { echo "usage: make install-tf VERSION=1.15.4 [DEST=bin]"; exit 2; }
	$(PYTHON) -m tools.install_tf "$(VERSION)" $(or $(DEST),bin)

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
	$(PYTHON) -m tools.gen_jsonschema
ifeq ($(CHECK),1)
	@git diff --exit-code --stat -- modules schemas/tfvars || { \
		echo ""; \
		echo "Generated output drifted from what is committed."; \
		echo "Never hand-edit modules/ — fix the generator or an override,"; \
		echo "run 'make generate', and commit the result."; \
		exit 1; }
	@test -z "$$(git status --porcelain -- modules schemas/tfvars)" || { \
		echo ""; \
		echo "Generated but UNCOMMITTED output (a generate=true registry"; \
		echo "entry whose module was never committed):"; \
		git status --porcelain -- modules schemas/tfvars; \
		echo "Commit it, or set generate=false in tools/registry.json."; \
		exit 1; }
endif

gen-env: ## Generate env roots for a tenant (TENANT=<label> [BACKEND=azurerm])
	@test -n "$(TENANT)" || { echo "usage: make gen-env TENANT=<label> [BACKEND=azurerm]"; exit 2; }
	$(PYTHON) -m tools.gen_env "$(TENANT)" $(BACKEND)

transform: ## Transform pulled API JSON into tfvars + imports (IN=<dir> TENANT=<name> [RESOURCE=<type>])
	@test -n "$(IN)" -a -n "$(TENANT)" || { echo "usage: make transform IN=pulls/<tenant> TENANT=<tenant> [RESOURCE=<type>]"; exit 2; }
	@failed=""; for rt in $$($(PYTHON) -c "from tools.registry import generated_types; print('\n'.join(generated_types()))"); do \
		if [ -n "$(RESOURCE)" ] && [ "$$rt" != "$(RESOURCE)" ]; then continue; fi; \
		if [ -f "$(IN)/$$rt.json" ]; then \
			$(PYTHON) -m tools.transform "$$rt" "$(IN)/$$rt.json" "$(TENANT)" || failed="$$failed $$rt"; \
		else \
			echo "skip $$rt (no $(IN)/$$rt.json)"; \
		fi; \
	done; \
	test -z "$$failed" || { echo ""; echo "transform FAILED for:$$failed"; \
		echo "(fix the override map per the error above; successful outputs are already written)"; exit 1; }

fetch: ## Pull API JSON into pulls/<tenant> (TENANT=<name> [RESOURCE=<type>]; needs ZSCALER_*/ZIA_*/ZPA_* env, real creds — trusted env only)
	@test -n "$(TENANT)" || { echo "usage: make fetch TENANT=<tenant> [RESOURCE=<type>] (with ZSCALER_*/ZIA_*/ZPA_* env set)"; exit 2; }
	$(PYTHON) -m tools.fetch "$(TENANT)" $(RESOURCE)

fetch-diag: ## Probe TLS to the fetcher's hosts under system trust and +bundle
	$(PYTHON) -m tools.fetch --diag

test-modules: ## Run mock-provider terraform tests across all generated modules
	@set -e; for d in modules/*/; do \
		echo "== $$d"; \
		$(TF) -chdir=$$d init -backend=false -input=false > /dev/null; \
		$(TF) -chdir=$$d test; \
		rm -rf $$d/.terraform $$d/.terraform.lock.hcl; \
	done

test-envs: ## Run mock-provider smoke tests across a tenant's env roots (TENANT=<label>)
	@test -n "$(TENANT)" || { echo "usage: make test-envs TENANT=<label>"; exit 2; }
	@set -e; for d in envs/$(TENANT)/*/; do \
		echo "== $$d"; \
		$(TF) -chdir=$$d init -backend=false -input=false > /dev/null; \
		$(TF) -chdir=$$d test; \
	done

validate-imports: ## Validate fixture import addresses against a tenant's roots (TENANT=<label>)
	@test -n "$(TENANT)" || { echo "usage: make validate-imports TENANT=<label>"; exit 2; }
	@set -e; for d in envs/$(TENANT)/*/; do \
		rt=$$(basename $$d); \
		fix="tools/tests/fixtures/transform/$$rt/expected_imports.tf"; \
		if [ ! -f "$$fix" ]; then \
			fix="tools/tests/fixtures/demo-expected/$${rt}_imports.tf"; \
		fi; \
		if [ -f "$$fix" ]; then \
			cp "$$fix" "$$d/imports_check.tf"; \
			$(TF) -chdir=$$d init -backend=false -input=false > /dev/null; \
			$(TF) -chdir=$$d validate || { rm -f "$$d/imports_check.tf"; exit 1; }; \
			rm -f "$$d/imports_check.tf"; \
			echo "imports ok: $$rt"; \
		else \
			echo "skip $$rt (no fixture imports)"; \
		fi; \
	done

plan: ## Terraform plan for a tenant's roots (TENANT=<label> [RESOURCE=<type>] [BACKEND_CONFIG=<file>]; real creds via env)
	@test -n "$(TENANT)" || { echo "usage: make plan TENANT=<label> [RESOURCE=<type>] [BACKEND_CONFIG=<file>]"; exit 2; }
	@set -e; planned=0; for d in envs/$(TENANT)/$(or $(RESOURCE),*)/; do \
		test -d "$$d" || continue; \
		rt=$$(basename $$d); \
		vf="$(abspath config/$(TENANT))/$$rt.auto.tfvars.json"; \
		test -f "$$vf" || { echo "skip $$rt (no $$vf)"; continue; }; \
		if grep -q '^  backend "' "$$d/main.tf" && [ -z "$(BACKEND_CONFIG)" ]; then \
			echo "error: $$rt declares a remote backend; run with BACKEND_CONFIG=<file>"; \
			echo "(copy backend.conf.example, fill the values, pass BACKEND_CONFIG=backend.conf)"; \
			exit 1; fi; \
		echo "== plan $$rt"; \
		$(TF) -chdir=$$d init -input=false $(if $(BACKEND_CONFIG),-reconfigure -backend-config="$(abspath $(BACKEND_CONFIG))" -backend-config="key=$(TENANT)/$$rt.tfstate") > /dev/null; \
		$(TF) -chdir=$$d plan -input=false -var-file="$$vf" $(if $(SAVE),-out=tfplan); \
		planned=$$((planned+1)); \
	done; \
	test $$planned -gt 0 || { echo "error: no roots planned for TENANT=$(TENANT) (typo? missing config/?)"; exit 1; }

plan-changed: ## Plan only the (tenant, resource) pairs changed vs BASE (default origin/main); SAVE/BACKEND_CONFIG pass through
	@set -e; $(PYTHON) -m tools.changed "$(or $(BASE),origin/main)" > .plan-changed.tmp; \
	if ! [ -s .plan-changed.tmp ]; then rm -f .plan-changed.tmp; echo "nothing to plan — no plannable changes vs $(or $(BASE),origin/main)"; exit 0; fi; \
	while read t rt; do \
		$(MAKE) plan TENANT=$$t RESOURCE=$$rt $(if $(SAVE),SAVE=1) $(if $(BACKEND_CONFIG),BACKEND_CONFIG=$(BACKEND_CONFIG)) || { rm -f .plan-changed.tmp; exit 1; }; \
	done < .plan-changed.tmp; \
	rm -f .plan-changed.tmp

assert-clean: ## Exit 0 only when every saved plan is no-op (imports allowed) — the drift-PR auto-merge gate ([TENANT=<label>] [RESOURCE=<type>])
	@set -e; checked=0; dirty=0; for d in envs/$(or $(TENANT),*)/$(or $(RESOURCE),*)/; do \
		test -f "$$d/tfplan" || continue; \
		rt=$$(basename $$d); t=$$(basename $$(dirname $$d)); \
		checked=$$((checked+1)); \
		changes=$$($(TF) -chdir=$$d show -json tfplan | $(PYTHON) -c "import json,sys; p=json.load(sys.stdin); print(sum(1 for rc in p.get('resource_changes') or [] if set((rc.get('change') or {}).get('actions') or []) - set(['no-op'])))"); \
		if [ "$$changes" != "0" ]; then \
			echo "NOT CLEAN: $$t/$$rt plan contains $$changes change(s) beyond imports"; \
			dirty=$$((dirty+1)); fi; \
	done; \
	test $$checked -gt 0 || { echo "error: no saved plans to check — run make plan-changed SAVE=1 first"; exit 1; }; \
	test $$dirty -eq 0 || { echo ""; echo "tenant moved since fetch (or transform disagrees) — do NOT auto-merge; re-run drift"; exit 1; }; \
	echo "all $$checked saved plan(s) clean (no-op/imports only)"

apply: ## Apply ONLY saved plans from 'make plan SAVE=1' ([TENANT=<label>] [RESOURCE=<type>] [BACKEND_CONFIG=<file>] [ALLOW_DESTROY=1])
	@set -e; applied=0; for d in envs/$(or $(TENANT),*)/$(or $(RESOURCE),*)/; do \
		test -f "$$d/tfplan" || continue; \
		rt=$$(basename $$d); t=$$(basename $$(dirname $$d)); \
		echo "== apply $$t/$$rt"; \
		$(TF) -chdir=$$d init -input=false $(if $(BACKEND_CONFIG),-reconfigure -backend-config="$(abspath $(BACKEND_CONFIG))" -backend-config="key=$$t/$$rt.tfstate") > /dev/null; \
		destroys=$$($(TF) -chdir=$$d show -json tfplan | $(PYTHON) -c "import json,sys; p=json.load(sys.stdin); print(sum(1 for rc in p.get('resource_changes') or [] if 'delete' in ((rc.get('change') or {}).get('actions') or [])))"); \
		if [ "$$destroys" != "0" ] && [ -z "$(ALLOW_DESTROY)" ]; then \
			echo "error: $$t/$$rt saved plan destroys (or replaces) $$destroys resource(s) — refused."; \
			echo "Review that plan; if the destroys are intended, re-run with ALLOW_DESTROY=1."; \
			exit 1; fi; \
		$(TF) -chdir=$$d apply -input=false tfplan; \
		rm -f "$$d/tfplan"; \
		applied=$$((applied+1)); \
	done; \
	test $$applied -gt 0 || { echo "error: no saved plans found — run 'make plan SAVE=1 ...' (or plan-changed SAVE=1) first; apply's scope IS the saved plans"; exit 1; }

drift: ## Fetch + transform + report config diff (TENANT=<label> [RESOURCE=<type>]; real creds via env)
	@test -n "$(TENANT)" || { echo "usage: make drift TENANT=<label> [RESOURCE=<type>]"; exit 2; }
	$(MAKE) fetch TENANT=$(TENANT) $(if $(RESOURCE),RESOURCE=$(RESOURCE))
	$(MAKE) transform IN=pulls/$(TENANT) TENANT=$(TENANT) $(if $(RESOURCE),RESOURCE=$(RESOURCE))
	@if [ -n "$$(git status --porcelain config/$(TENANT) imports/$(TENANT) 2>/dev/null)" ]; then \
		echo ""; echo "DRIFT DETECTED (tenant differs from committed config):"; \
		git status --porcelain config/$(TENANT) imports/$(TENANT); \
		git --no-pager diff --stat config/$(TENANT) 2>/dev/null; \
		exit 3; \
	else \
		echo "no drift: tenant matches committed config"; \
	fi

check-envs: ## Regenerate committed tenants' env roots and fail on drift
	@set -e; for t in $$(ls envs); do $(PYTHON) -m tools.gen_env "$$t" > /dev/null; done
	@test -z "$$(git status --porcelain -- envs)" || { \
		echo ""; echo "envs/ drifted from the generator output:"; \
		git status --porcelain -- envs; \
		echo "Run make gen-env for each tenant and commit."; exit 1; }

demo: ## Materialize the demo tenant from the public demo dataset (config/demo + imports/demo)
	@set -e; for rt in $$($(PYTHON) -c "from tools.registry import generated_types; print('\n'.join(generated_types()))"); do \
		f="tools/tests/fixtures/demo/$$rt.json"; \
		test -f "$$f" || { echo "missing $$f"; exit 1; }; \
		$(PYTHON) -m tools.transform "$$rt" "$$f" demo; \
	done

check-demo: ## Fail if the committed demo tenant drifts from the pipeline output
	$(MAKE) demo > /dev/null 2>&1
	@test -z "$$(git status --porcelain -- config/demo imports/demo)" || { \
		echo ""; echo "demo tenant drifted from pipeline output over the demo dataset:"; \
		git status --porcelain -- config/demo imports/demo; \
		echo "Run 'make demo' and commit (or fix the regression it reveals)."; exit 1; }

lint: ## Semantic config lint — pasted chars, URL/IP syntax, set duplicates, order collisions, category shadowing (TENANT=<label>)
	@test -n "$(TENANT)" || { echo "usage: make lint TENANT=<label>"; exit 2; }
	$(PYTHON) -m tools.lint "$(TENANT)"

fmt-config: ## Rewrite a tenant's config files in canonical transform form (TENANT=<label>)
	@test -n "$(TENANT)" || { echo "usage: make fmt-config TENANT=<label>"; exit 2; }
	$(PYTHON) -m tools.fmt_config "$(TENANT)"

typecheck: ## Type-check a tenant's config against the provider schemas (stdlib; TENANT=<label>)
	@test -n "$(TENANT)" || { echo "usage: make typecheck TENANT=<label>"; exit 2; }
	$(PYTHON) -m tools.typecheck "$(TENANT)"

conformance: ## Schema-driven adversarial conformance report (synthesize -> transform -> typecheck) for every registry resource
	$(PYTHON) -m tools.conformance

validate-config: ## Validate config/ against generated JSON Schemas (dev-only; jsonschema via python or uv)
	@if $(PYTHON) -c "import jsonschema" 2>/dev/null; then \
		$(PYTHON) -m tools.validate_config; \
	elif command -v uv >/dev/null 2>&1; then \
		uv run --quiet --with jsonschema python -m tools.validate_config; \
	else \
		echo "WARNING: no python 'jsonschema' and no uv - skipping config validation"; \
		echo "(dev-only check; never required in restricted environments)"; \
	fi

update-goldens: ## Re-bless generator golden fixtures from current output
	$(PYTHON) -m tools.gen_module
	rm -rf tools/tests/fixtures/gen
	mkdir -p tools/tests/fixtures/gen/zpa_segment_group
	cp modules/zpa_segment_group/variables.tf modules/zpa_segment_group/main.tf \
		modules/zpa_segment_group/outputs.tf modules/zpa_segment_group/versions.tf \
		tools/tests/fixtures/gen/zpa_segment_group/
	mkdir -p tools/tests/fixtures/gen/zia_url_categories
	$(PYTHON) -c "from tools.tfschema import load_resource; from tools.gen_module import render_variables, render_main, render_outputs, render_versions, _fmt; rt = 'zia_url_categories'; rs = load_resource(rt); base = 'tools/tests/fixtures/gen/zia_url_categories/'; open(base+'variables.tf','w').write(_fmt(render_variables(rt, rs))); open(base+'main.tf','w').write(_fmt(render_main(rt, rs))); open(base+'outputs.tf','w').write(_fmt(render_outputs(rt, rs))); open(base+'versions.tf','w').write(_fmt(render_versions(rt, rs)))"
	mkdir -p tools/tests/fixtures/gen/zpa_server_group
	$(PYTHON) -c "from tools.tfschema import load_resource; from tools.gen_module import render_variables, render_main, render_outputs, render_versions, _fmt; rt = 'zpa_server_group'; rs = load_resource(rt); base = 'tools/tests/fixtures/gen/zpa_server_group/'; open(base+'variables.tf','w').write(_fmt(render_variables(rt, rs))); open(base+'main.tf','w').write(_fmt(render_main(rt, rs))); open(base+'outputs.tf','w').write(_fmt(render_outputs(rt, rs))); open(base+'versions.tf','w').write(_fmt(render_versions(rt, rs)))"
	mkdir -p tools/tests/fixtures/gen/zpa_application_segment
	$(PYTHON) -c "from tools.tfschema import load_resource; from tools.gen_module import render_variables, render_main, render_outputs, render_versions, _fmt; rt = 'zpa_application_segment'; rs = load_resource(rt); base = 'tools/tests/fixtures/gen/zpa_application_segment/'; open(base+'variables.tf','w').write(_fmt(render_variables(rt, rs))); open(base+'main.tf','w').write(_fmt(render_main(rt, rs))); open(base+'outputs.tf','w').write(_fmt(render_outputs(rt, rs))); open(base+'versions.tf','w').write(_fmt(render_versions(rt, rs)))"
	mkdir -p tools/tests/fixtures/gen/zia_location_management
	$(PYTHON) -c "from tools.tfschema import load_resource; from tools.gen_module import render_variables, render_main, render_outputs, render_versions, _fmt; rt = 'zia_location_management'; rs = load_resource(rt); base = 'tools/tests/fixtures/gen/zia_location_management/'; open(base+'variables.tf','w').write(_fmt(render_variables(rt, rs))); open(base+'main.tf','w').write(_fmt(render_main(rt, rs))); open(base+'outputs.tf','w').write(_fmt(render_outputs(rt, rs))); open(base+'versions.tf','w').write(_fmt(render_versions(rt, rs)))"
	mkdir -p tools/tests/fixtures/gen/zia_ssl_inspection_rules
	$(PYTHON) -c "from tools.tfschema import load_resource; from tools.gen_module import render_variables, render_main, render_outputs, render_versions, _fmt; rt = 'zia_ssl_inspection_rules'; rs = load_resource(rt); base = 'tools/tests/fixtures/gen/zia_ssl_inspection_rules/'; open(base+'variables.tf','w').write(_fmt(render_variables(rt, rs))); open(base+'main.tf','w').write(_fmt(render_main(rt, rs))); open(base+'outputs.tf','w').write(_fmt(render_outputs(rt, rs))); open(base+'versions.tf','w').write(_fmt(render_versions(rt, rs)))"
	mkdir -p tools/tests/fixtures/gen/zia_cloud_app_control_rule
	$(PYTHON) -c "from tools.tfschema import load_resource; from tools.gen_module import render_variables, render_main, render_outputs, render_versions, _fmt; rt = 'zia_cloud_app_control_rule'; rs = load_resource(rt); base = 'tools/tests/fixtures/gen/zia_cloud_app_control_rule/'; open(base+'variables.tf','w').write(_fmt(render_variables(rt, rs))); open(base+'main.tf','w').write(_fmt(render_main(rt, rs))); open(base+'outputs.tf','w').write(_fmt(render_outputs(rt, rs))); open(base+'versions.tf','w').write(_fmt(render_versions(rt, rs)))"
	mkdir -p tools/tests/fixtures/gen/zia_url_filtering_rules
	$(PYTHON) -c "from tools.tfschema import load_resource; from tools.gen_module import render_variables, render_main, render_outputs, render_versions, _fmt; rt = 'zia_url_filtering_rules'; rs = load_resource(rt); base = 'tools/tests/fixtures/gen/zia_url_filtering_rules/'; open(base+'variables.tf','w').write(_fmt(render_variables(rt, rs))); open(base+'main.tf','w').write(_fmt(render_main(rt, rs))); open(base+'outputs.tf','w').write(_fmt(render_outputs(rt, rs))); open(base+'versions.tf','w').write(_fmt(render_versions(rt, rs)))"
	mkdir -p tools/tests/fixtures/gen/zia_rule_labels
	$(PYTHON) -c "from tools.tfschema import load_resource; from tools.gen_module import render_variables, render_main, render_outputs, render_versions, _fmt; rt = 'zia_rule_labels'; rs = load_resource(rt); base = 'tools/tests/fixtures/gen/zia_rule_labels/'; open(base+'variables.tf','w').write(_fmt(render_variables(rt, rs))); open(base+'main.tf','w').write(_fmt(render_main(rt, rs))); open(base+'outputs.tf','w').write(_fmt(render_outputs(rt, rs))); open(base+'versions.tf','w').write(_fmt(render_versions(rt, rs)))"
	mkdir -p tools/tests/fixtures/gen/zpa_app_connector_group
	$(PYTHON) -c "from tools.tfschema import load_resource; from tools.gen_module import render_variables, render_main, render_outputs, render_versions, _fmt; rt = 'zpa_app_connector_group'; rs = load_resource(rt); base = 'tools/tests/fixtures/gen/zpa_app_connector_group/'; open(base+'variables.tf','w').write(_fmt(render_variables(rt, rs))); open(base+'main.tf','w').write(_fmt(render_main(rt, rs))); open(base+'outputs.tf','w').write(_fmt(render_outputs(rt, rs))); open(base+'versions.tf','w').write(_fmt(render_versions(rt, rs)))"
	mkdir -p tools/tests/fixtures/gen/zpa_application_server
	$(PYTHON) -c "from tools.tfschema import load_resource; from tools.gen_module import render_variables, render_main, render_outputs, render_versions, _fmt; rt = 'zpa_application_server'; rs = load_resource(rt); base = 'tools/tests/fixtures/gen/zpa_application_server/'; open(base+'variables.tf','w').write(_fmt(render_variables(rt, rs))); open(base+'main.tf','w').write(_fmt(render_main(rt, rs))); open(base+'outputs.tf','w').write(_fmt(render_outputs(rt, rs))); open(base+'versions.tf','w').write(_fmt(render_versions(rt, rs)))"
	mkdir -p tools/tests/fixtures/gen/zpa_policy_access_rule
	$(PYTHON) -c "from tools.tfschema import load_resource; from tools.gen_module import render_variables, render_main, render_outputs, render_versions, _fmt; rt = 'zpa_policy_access_rule'; rs = load_resource(rt); base = 'tools/tests/fixtures/gen/zpa_policy_access_rule/'; open(base+'variables.tf','w').write(_fmt(render_variables(rt, rs))); open(base+'main.tf','w').write(_fmt(render_main(rt, rs))); open(base+'outputs.tf','w').write(_fmt(render_outputs(rt, rs))); open(base+'versions.tf','w').write(_fmt(render_versions(rt, rs)))"
	mkdir -p tools/tests/fixtures/gen/zcc_failopen_policy
	$(PYTHON) -c "from tools.tfschema import load_resource; from tools.gen_module import render_variables, render_main, render_outputs, render_versions, _fmt; rt = 'zcc_failopen_policy'; rs = load_resource(rt); base = 'tools/tests/fixtures/gen/zcc_failopen_policy/'; open(base+'variables.tf','w').write(_fmt(render_variables(rt, rs))); open(base+'main.tf','w').write(_fmt(render_main(rt, rs))); open(base+'outputs.tf','w').write(_fmt(render_outputs(rt, rs))); open(base+'versions.tf','w').write(_fmt(render_versions(rt, rs)))"
	mkdir -p tools/tests/fixtures/gen/zcc_forwarding_profile
	$(PYTHON) -c "from tools.tfschema import load_resource; from tools.gen_module import render_variables, render_main, render_outputs, render_versions, _fmt; rt = 'zcc_forwarding_profile'; rs = load_resource(rt); base = 'tools/tests/fixtures/gen/zcc_forwarding_profile/'; open(base+'variables.tf','w').write(_fmt(render_variables(rt, rs))); open(base+'main.tf','w').write(_fmt(render_main(rt, rs))); open(base+'outputs.tf','w').write(_fmt(render_outputs(rt, rs))); open(base+'versions.tf','w').write(_fmt(render_versions(rt, rs)))"
	mkdir -p tools/tests/fixtures/gen/zcc_trusted_network
	$(PYTHON) -c "from tools.tfschema import load_resource; from tools.gen_module import render_variables, render_main, render_outputs, render_versions, _fmt; rt = 'zcc_trusted_network'; rs = load_resource(rt); base = 'tools/tests/fixtures/gen/zcc_trusted_network/'; open(base+'variables.tf','w').write(_fmt(render_variables(rt, rs))); open(base+'main.tf','w').write(_fmt(render_main(rt, rs))); open(base+'outputs.tf','w').write(_fmt(render_outputs(rt, rs))); open(base+'versions.tf','w').write(_fmt(render_versions(rt, rs)))"
	mkdir -p tools/tests/fixtures/gen/zcc_web_privacy
	$(PYTHON) -c "from tools.tfschema import load_resource; from tools.gen_module import render_variables, render_main, render_outputs, render_versions, _fmt; rt = 'zcc_web_privacy'; rs = load_resource(rt); base = 'tools/tests/fixtures/gen/zcc_web_privacy/'; open(base+'variables.tf','w').write(_fmt(render_variables(rt, rs))); open(base+'main.tf','w').write(_fmt(render_main(rt, rs))); open(base+'outputs.tf','w').write(_fmt(render_outputs(rt, rs))); open(base+'versions.tf','w').write(_fmt(render_versions(rt, rs)))"

update-demo-goldens: ## Re-bless demo-expected fixtures from the current pipeline output
	@mkdir -p tools/tests/fixtures/demo-expected
	@set -e; for rt in $$($(PYTHON) -c "from tools.registry import generated_types; print('\n'.join(generated_types()))"); do \
		f="tools/tests/fixtures/demo/$$rt.json"; \
		if [ ! -f "$$f" ]; then echo "skip $$rt (no demo file)"; continue; fi; \
		$(PYTHON) -c "\
import json, os, sys; sys.path.insert(0, '.'); \
from tools.transform import load_override, transform_items, render_tfvars, render_imports; \
rt='$$rt'; \
raw=json.load(open('$$f')); \
ov=load_override(rt); \
items, originals, _=transform_items(raw, rt, ov); \
open('tools/tests/fixtures/demo-expected/'+rt+'.tfvars.json','w').write(render_tfvars(items)); \
open('tools/tests/fixtures/demo-expected/'+rt+'_imports.tf','w').write(render_imports(rt, originals, ov)); \
print('blessed', rt)" 2>/dev/null; \
	done
