PYTHON ?= python3
TF     ?= terraform

# Scope glob for the per-root targets (plan/apply/assert-clean/plan-report/
# clean-plans/stage-imports/unstage-imports): a resource type, a glob
# (zia_*), or a SINGLE product token (zia|zpa|zcc) which expands to
# <product>_*. Multi-selector scoping ("zia zpa") is fetch/drift-only —
# the python side expands those.
SCOPE_GLOB = $(if $(RESOURCE),$(if $(word 2,$(RESOURCE)),$(RESOURCE),$(if $(filter zia zpa zcc,$(RESOURCE)),$(RESOURCE)_*,$(RESOURCE))),*)

.PHONY: help env install-tf bump-check mine plan-report clean clean-plans unlock forget stage-imports unstage-imports lock test test-floor validate schemas generate gen-env transform fetch fetch-diag update-goldens update-demo-goldens test-modules test-envs validate-imports plan plan-changed drift-report assert-clean apply drift check-envs validate-config demo check-demo lint fmt-config typecheck conformance

# Company/deployment extensions: a private repo adds its own targets and
# variable overrides in local.mk — NEVER by editing this file, which is
# template-owned and overwritten on template updates. local.mk is not
# shipped by the template and is yours to commit privately.
-include local.mk

help: ## List available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'

env: ## Print toolchain versions (diagnostic)
	@uname -sm
	@$(PYTHON) --version 2>&1 || echo "python3: not found"
	@$(MAKE) --version 2>/dev/null | head -1
	@$(TF) version 2>/dev/null | head -1 || echo "terraform: not found"
	@docker --version 2>/dev/null || echo "docker: not found"

install-tf: ## Download+checksum-verify a pinned terraform (VERSION=<v> [DEST=bin]); then PATH it or pass TF=bin/terraform
	@test -n "$(VERSION)" || { echo "usage: make install-tf VERSION=1.15.4 [DEST=bin]"; exit 2; }
	$(PYTHON) -m tools.install_tf "$(VERSION)" "$(or $(DEST),bin)"

bump-check: ## Check pinned providers + terraform for newer releases (tool exits 4 on updates; make flattens to 2 — a red scheduled run IS the notification)
	TF="$(TF)" $(PYTHON) -m tools.bump_check

mine: ## Mine pinned provider Go source for quirks vs override coverage (tool exits 4 on NEW missing; UPDATE_BASELINE=1 blesses current findings; needs network — see tools/MINING.md)
	$(PYTHON) -m tools.mine

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
	@echo "$(TENANT)" | grep -qE '^[A-Za-z0-9_.-]+$$' || { echo "error: TENANT must match [A-Za-z0-9_.-]+ (got '$(TENANT)')"; exit 2; }
	$(PYTHON) -m tools.gen_env "$(TENANT)" $(BACKEND)

transform: ## Transform pulled API JSON into tfvars + imports (IN=<dir> TENANT=<name> [RESOURCE=<type>])
	@test -n "$(IN)" -a -n "$(TENANT)" || { echo "usage: make transform IN=pulls/<tenant> TENANT=<tenant> [RESOURCE=<type>]"; exit 2; }
	@echo "$(TENANT)" | grep -qE '^[A-Za-z0-9_.-]+$$' || { echo "error: TENANT must match [A-Za-z0-9_.-]+ (got '$(TENANT)')"; exit 2; }
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

fetch: ## Pull API JSON into pulls/<tenant> (TENANT=<name> [RESOURCE="<type|product> ..."]; products zia/zpa/zcc expand; real creds via env — trusted env only)
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
	@echo "$(TENANT)" | grep -qE '^[A-Za-z0-9_.-]+$$' || { echo "error: TENANT must match [A-Za-z0-9_.-]+ (got '$(TENANT)')"; exit 2; }
	@set -e; for d in envs/$(TENANT)/*/; do \
		echo "== $$d"; \
		$(TF) -chdir=$$d init -backend=false -input=false > /dev/null; \
		$(TF) -chdir=$$d test; \
	done

validate-imports: ## Validate fixture import addresses against a tenant's roots (TENANT=<label>)
	@test -n "$(TENANT)" || { echo "usage: make validate-imports TENANT=<label>"; exit 2; }
	@echo "$(TENANT)" | grep -qE '^[A-Za-z0-9_.-]+$$' || { echo "error: TENANT must match [A-Za-z0-9_.-]+ (got '$(TENANT)')"; exit 2; }
	@set -e; for d in envs/$(TENANT)/*/; do \
		rt=$$(basename $$d); \
		fix="tools/tests/fixtures/transform/$$rt/expected_imports.tf"; \
		if [ ! -f "$$fix" ]; then \
			fix="tools/tests/fixtures/demo-expected/$${rt}_imports.tf"; \
		fi; \
		if [ -f "$$fix" ]; then \
			cp "$$fix" "$$d/imports_check.tf"; \
			{ $(TF) -chdir=$$d init -backend=false -input=false > /dev/null && $(TF) -chdir=$$d validate; } || { rm -f "$$d/imports_check.tf"; exit 1; }; \
			rm -f "$$d/imports_check.tf"; \
			echo "imports ok: $$rt"; \
		else \
			echo "skip $$rt (no fixture imports)"; \
		fi; \
	done

lock: ## Pin provider HASHES per env root (TENANT=<label>; one registry fetch per product, copied to sibling roots; commit the lock files)
	@test -n "$(TENANT)" || { echo "usage: make lock TENANT=<label>"; exit 2; }
	@set -e; locked=0; for prefix in zia zpa zcc; do \
		first=""; \
		for d in envs/$(TENANT)/$${prefix}_*/; do \
			test -d "$$d" || continue; \
			if [ -z "$$first" ]; then \
				first="$$d"; \
				echo "== lock $$d (3 platforms — downloads provider archives once per product)"; \
				$(TF) -chdir=$$d providers lock -platform=linux_amd64 -platform=darwin_amd64 -platform=darwin_arm64; \
			else \
				cp "$${first}.terraform.lock.hcl" "$$d.terraform.lock.hcl"; \
			fi; \
			locked=$$((locked+1)); \
		done; \
	done; \
	test $$locked -gt 0 || { echo "error: no env roots found for TENANT=$(TENANT) — run make gen-env first"; exit 1; }; \
	echo "locked $$locked root(s); commit envs/$(TENANT)/**/.terraform.lock.hcl"

plan: ## Terraform plan for a tenant's roots (TENANT=<label> [RESOURCE=<type>] [BACKEND_CONFIG=<file>]; real creds via env)
	@test -n "$(TENANT)" || { echo "usage: make plan TENANT=<label> [RESOURCE=<type>] [BACKEND_CONFIG=<file>]"; exit 2; }
	@echo "$(TENANT)" | grep -qE '^[A-Za-z0-9_.-]+$$' || { echo "error: TENANT must match [A-Za-z0-9_.-]+ (got '$(TENANT)')"; exit 2; }
	@set -e; planned=0; for d in envs/$(TENANT)/$(SCOPE_GLOB)/; do \
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

clean: ## Remove run artifacts: saved plans, staged import/move copies, reports/, temp files (committed files, pulls/, caches untouched)
	@$(MAKE) clean-plans > /dev/null 2>&1 || true
	@removed=0; for f in envs/*/*/*_imports.tf envs/*/*/*_moves.tf envs/*/*/.state-list.tmp; do \
		test -f "$$f" || continue; \
		rm -f "$$f"; removed=$$((removed+1)); \
	done; \
	rm -rf reports .plan-changed.tmp; \
	echo "clean: removed staged copies ($$removed), saved plans, reports/, temp files"

clean-plans: ## Delete saved tfplan artifacts ([TENANT=<label>] [RESOURCE=<type>]) — run before any fresh plan set; stale plans from a failed/cancelled run otherwise ride into the next apply
	@removed=0; for d in envs/$(or $(TENANT),*)/$(SCOPE_GLOB)/; do \
		test -f "$$d/tfplan" || continue; \
		rm -f "$$d/tfplan"; echo "removed $$d""tfplan"; removed=$$((removed+1)); \
	done; \
	echo "$$removed stale plan(s) removed"

plan-changed: ## Plan only the (tenant, resource) pairs changed vs BASE (default origin/main); SAVE/BACKEND_CONFIG pass through
	@$(MAKE) clean-plans > /dev/null
	@set -e; $(PYTHON) -m tools.changed "$(or $(BASE),origin/main)" > .plan-changed.tmp; \
	if ! [ -s .plan-changed.tmp ]; then rm -f .plan-changed.tmp; echo "nothing to plan — no plannable changes vs $(or $(BASE),origin/main)"; exit 0; fi; \
	while read t rt; do \
		$(MAKE) plan TENANT=$$t RESOURCE=$$rt $(if $(SAVE),SAVE=1) $(if $(BACKEND_CONFIG),BACKEND_CONFIG=$(BACKEND_CONFIG)) || { rm -f .plan-changed.tmp; exit 1; }; \
	done < .plan-changed.tmp; \
	rm -f .plan-changed.tmp

drift-report: ## Render the drift summary + audit attribution to reports/<tenant>/drift.md (TENANT=<label> [AUDIT_HOURS=24]) — the PR body and the publishable artifact
	@test -n "$(TENANT)" || { echo "usage: make drift-report TENANT=<label> [AUDIT_HOURS=24]"; exit 2; }
	@echo "$(TENANT)" | grep -qE '^[A-Za-z0-9_.-]+$$' || { echo "error: TENANT must match [A-Za-z0-9_.-]+ (got '$(TENANT)')"; exit 2; }
	@mkdir -p reports/$(TENANT)
	@$(PYTHON) -m tools.drift_summary "$(TENANT)" > reports/$(TENANT)/drift.md
	@$(PYTHON) -m tools.audit "$(TENANT)" $(or $(AUDIT_HOURS),24) >> reports/$(TENANT)/drift.md
	@echo "wrote reports/$(TENANT)/drift.md"

stage-imports: ## Copy import (and moved) blocks into env roots (TENANT=<label> [RESOURCE=<type>] [STATE_AWARE=1 [BACKEND_CONFIG=<file>]]) — STATE_AWARE filters out already-managed imports so re-runs adopt only the delta
	@test -n "$(TENANT)" || { echo "usage: make stage-imports TENANT=<label> [RESOURCE=<type>] [STATE_AWARE=1] [BACKEND_CONFIG=<file>]"; exit 2; }
	@set -e; staged=0; sources=0; for f in imports/$(TENANT)/$(SCOPE_GLOB)_imports.tf imports/$(TENANT)/$(SCOPE_GLOB)_moves.tf; do \
		test -f "$$f" || continue; \
		sources=$$((sources+1)); \
		base=$$(basename "$$f"); \
		rt=$$(echo "$$base" | sed 's/_imports\.tf$$//; s/_moves\.tf$$//'); \
		d="envs/$(TENANT)/$$rt"; \
		test -d "$$d" || { echo "skip $$base (no env root $$d — run make gen-env)"; continue; }; \
		case "$$base" in \
		*_imports.tf) \
			if [ -n "$(STATE_AWARE)" ]; then \
				$(TF) -chdir="$$d" init -input=false $(if $(BACKEND_CONFIG),-reconfigure -backend-config="$(abspath $(BACKEND_CONFIG))" -backend-config="key=$(TENANT)/$$rt.tfstate") > /dev/null; \
				$(TF) -chdir="$$d" state list > "$$d/.state-list.tmp" 2>/dev/null || : > "$$d/.state-list.tmp"; \
				$(PYTHON) -m tools.filter_imports "$$f" "$$d/.state-list.tmp" > "$$d/$$base"; \
				rm -f "$$d/.state-list.tmp"; \
				if ! [ -s "$$d/$$base" ]; then \
					rm -f "$$d/$$base"; \
					echo "skip $$base (every import already managed — delta is empty)"; \
					continue; \
				fi; \
			else \
				cp "$$f" "$$d/$$base"; \
			fi ;; \
		*) \
			cp "$$f" "$$d/$$base" ;; \
		esac; \
		echo "staged $$d/$$base"; \
		staged=$$((staged+1)); \
	done; \
	test $$sources -gt 0 || { echo "error: nothing to stage for TENANT=$(TENANT) (no imports/$(TENANT)/*_imports.tf — run make transform first)"; exit 1; }; \
	test $$staged -gt 0 || echo "NOTE: 0 staged — every import is already managed; the delta is empty and the plan will be a no-op"

unstage-imports: ## Remove staged import/moved blocks from env roots after apply (TENANT=<label> [RESOURCE=<type>])
	@test -n "$(TENANT)" || { echo "usage: make unstage-imports TENANT=<label> [RESOURCE=<type>]"; exit 2; }
	@removed=0; for f in envs/$(TENANT)/$(SCOPE_GLOB)/*_imports.tf envs/$(TENANT)/$(SCOPE_GLOB)/*_moves.tf; do \
		test -f "$$f" || continue; \
		rm -f "$$f"; echo "removed $$f"; removed=$$((removed+1)); \
	done; \
	echo "$$removed file(s) removed"

plan-report: ## Render saved plans to reports/plan.md — counts-first summary table for the approval reviewer, full plan text below ([TENANT=<label>] [RESOURCE=<type>])
	@set -e; mkdir -p reports; out="reports/plan.md"; body="reports/.body.tmp"; rows="reports/.rows.tmp"; \
	: > "$$body"; : > "$$rows"; found=0; destroys_total=0; \
	for d in envs/$(or $(TENANT),*)/$(SCOPE_GLOB)/; do \
		test -f "$$d/tfplan" || continue; \
		rt=$$(basename "$$d"); t=$$(basename "$$(dirname "$$d")"); \
		found=$$((found+1)); \
		$(TF) -chdir="$$d" show -json tfplan > "$$d/.plan.json"; \
		info=$$($(PYTHON) -m tools.plan_summary "$$t/$$rt" < "$$d/.plan.json"); \
		rm -f "$$d/.plan.json"; \
		echo "$$info" | head -1 >> "$$rows"; \
		n=$$(echo "$$info" | tail -1); destroys_total=$$((destroys_total+n)); \
		printf '### %s/%s\n\n```\n' "$$t" "$$rt" >> "$$body"; \
		$(TF) -chdir="$$d" show -no-color tfplan >> "$$body"; \
		printf '\n```\n\n' >> "$$body"; \
	done; \
	test $$found -gt 0 || { rm -f "$$body" "$$rows"; echo "error: no saved plans — run make plan-changed SAVE=1 (or make plan SAVE=1) first"; exit 1; }; \
	{ printf '## Terraform plan\n\n'; \
	  if [ "$$destroys_total" -gt 0 ]; then \
		printf '> :warning: **%d DESTROY(S) in this plan set — read those roots first. Applies refuse destroys without ALLOW_DESTROY=1.**\n\n' "$$destroys_total"; \
	  fi; \
	  printf '| root | import | add | change | destroy |\n|---|---|---|---|---|\n'; \
	  cat "$$rows"; printf '\n'; cat "$$body"; } > "$$out"; \
	rm -f "$$body" "$$rows"; \
	echo "wrote $$out ($$found plan(s), $$destroys_total destroy(s))"

assert-clean: ## Exit 0 only when every saved plan is no-op (imports allowed) — the drift-PR merge-readiness check ([TENANT=<label>] [RESOURCE=<type>])
	@set -e; checked=0; dirty=0; for d in envs/$(or $(TENANT),*)/$(SCOPE_GLOB)/; do \
		test -f "$$d/tfplan" || continue; \
		rt=$$(basename $$d); t=$$(basename $$(dirname $$d)); \
		checked=$$((checked+1)); \
		raw=$$($(TF) -chdir=$$d show -json tfplan); \
		changes=$$(printf '%s' "$$raw" | $(PYTHON) -c "import json,sys; p=json.load(sys.stdin); sys.exit(2) if 'format_version' not in p else print(sum(1 for r in (p.get('resource_changes') or []) if set((r.get('change') or {}).get('actions') or []) - set(['no-op'])))") || { \
			echo "error: $$t/$$rt: terraform show output is not plan JSON (terraform version skew between agents?) — re-run the plan stage"; \
			exit 1; }; \
		if [ "$$changes" != "0" ]; then \
			echo "NOT CLEAN: $$t/$$rt plan contains $$changes change(s) beyond imports"; \
			dirty=$$((dirty+1)); fi; \
	done; \
	test $$checked -gt 0 || { echo "error: no saved plans to check — run make plan-changed SAVE=1 first"; exit 1; }; \
	test $$dirty -eq 0 || { echo ""; echo "tenant moved since fetch (or transform disagrees) — do NOT auto-merge; re-run drift"; exit 1; }; \
	echo "all $$checked saved plan(s) clean (no-op/imports only)"

unlock: ## Break a stale state lock after a killed run (TENANT=<label> RESOURCE=<one type> LOCK_ID=<uuid from the lock error> [BACKEND_CONFIG=<file>])
	@test -n "$(TENANT)" -a -n "$(RESOURCE)" -a -n "$(LOCK_ID)" || { echo "usage: make unlock TENANT=<label> RESOURCE=<type> LOCK_ID=<uuid> [BACKEND_CONFIG=<file>]"; echo "(LOCK_ID is in the 'Error acquiring the state lock' message)"; exit 2; }
	@test -d "envs/$(TENANT)/$(RESOURCE)" || { echo "error: envs/$(TENANT)/$(RESOURCE) is not an env root — RESOURCE must be ONE concrete type (locks are per root)"; exit 2; }
	@echo "CAUTION: only break a lock whose holder is DEAD (a killed run)."
	@echo "If a pipeline run is currently active on this root, cancel it instead."
	$(TF) -chdir=envs/$(TENANT)/$(RESOURCE) init -input=false $(if $(BACKEND_CONFIG),-reconfigure -backend-config="$(abspath $(BACKEND_CONFIG))" -backend-config="key=$(TENANT)/$(RESOURCE).tfstate") > /dev/null
	$(TF) -chdir=envs/$(TENANT)/$(RESOURCE) force-unlock -force "$(LOCK_ID)"

forget: ## Remove an item from STATE without destroying it (TENANT=<label> RESOURCE=<one type> KEY=<config key> [BACKEND_CONFIG=<file>]) — the right way to de-scope an imported item; never ALLOW_DESTROY for this
	@test -n "$(TENANT)" -a -n "$(RESOURCE)" -a -n "$(KEY)" || { echo "usage: make forget TENANT=<label> RESOURCE=<type> KEY=<config-map key> [BACKEND_CONFIG=<file>]"; exit 2; }
	@test -d "envs/$(TENANT)/$(RESOURCE)" || { echo "error: envs/$(TENANT)/$(RESOURCE) is not an env root — RESOURCE must be ONE concrete type"; exit 2; }
	$(TF) -chdir=envs/$(TENANT)/$(RESOURCE) init -input=false $(if $(BACKEND_CONFIG),-reconfigure -backend-config="$(abspath $(BACKEND_CONFIG))" -backend-config="key=$(TENANT)/$(RESOURCE).tfstate") > /dev/null
	$(TF) -chdir=envs/$(TENANT)/$(RESOURCE) state rm 'module.$(RESOURCE).$(RESOURCE).this["$(KEY)"]'
	@echo "forgotten: the object still exists in the tenant; it is simply unmanaged now"

apply: ## Apply ONLY saved plans from 'make plan SAVE=1' ([TENANT=<label>] [RESOURCE=<type>] [BACKEND_CONFIG=<file>] [ALLOW_DESTROY=1] [ALLOW_NON_MAIN=1]) — refuses to run off $(or $(MAIN_BRANCH),main)
	@ref="$${BUILD_SOURCEBRANCH:-$${GITHUB_REF:-$${BITBUCKET_BRANCH:-}}}"; \
	if [ -z "$$ref" ]; then ref="refs/heads/$$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"; fi; \
	branch="$${ref#refs/heads/}"; \
	if [ "$$branch" != "$(or $(MAIN_BRANCH),main)" ] && [ -z "$(ALLOW_NON_MAIN)" ]; then \
		echo "error: apply refused from '$$branch' — only merged $(or $(MAIN_BRANCH),main) config gets applied."; \
		echo "(deliberate exception, e.g. testing: re-run with ALLOW_NON_MAIN=1; different default branch: MAIN_BRANCH=<name>)"; \
		exit 1; fi
	@set -e; applied=0; for d in envs/$(or $(TENANT),*)/$(SCOPE_GLOB)/; do \
		test -f "$$d/tfplan" || continue; \
		rt=$$(basename $$d); t=$$(basename $$(dirname $$d)); \
		echo "== apply $$t/$$rt"; \
		if grep -q '^  backend "' "$$d/main.tf" && [ -z "$(BACKEND_CONFIG)" ]; then \
			echo "error: $$rt declares a remote backend; run with BACKEND_CONFIG=<file>"; \
			echo "(copy backend.conf.example, fill the values, pass BACKEND_CONFIG=backend.conf)"; \
			exit 1; fi; \
		$(TF) -chdir=$$d init -input=false $(if $(BACKEND_CONFIG),-reconfigure -backend-config="$(abspath $(BACKEND_CONFIG))" -backend-config="key=$$t/$$rt.tfstate") > /dev/null; \
		raw=$$($(TF) -chdir=$$d show -json tfplan); \
		destroys=$$(printf '%s' "$$raw" | $(PYTHON) -c "import json,sys; p=json.load(sys.stdin); sys.exit(2) if 'format_version' not in p else print(sum(1 for r in (p.get('resource_changes') or []) if 'delete' in ((r.get('change') or {}).get('actions') or [])))") || { \
			echo "error: $$t/$$rt: terraform show output is not plan JSON (terraform version skew between the plan and apply agents?) — re-run the plan stage on a matching agent"; \
			exit 1; }; \
		if [ "$$destroys" != "0" ] && [ -z "$(ALLOW_DESTROY)" ]; then \
			echo "error: $$t/$$rt saved plan destroys (or replaces) $$destroys resource(s) — refused."; \
			echo "Review that plan; if the destroys are intended, re-run with ALLOW_DESTROY=1."; \
			exit 1; fi; \
		$(TF) -chdir=$$d apply -input=false tfplan; \
		rm -f "$$d/tfplan"; \
		applied=$$((applied+1)); \
	done; \
	test $$applied -gt 0 || { echo "error: no saved plans found — run 'make plan SAVE=1 ...' (or plan-changed SAVE=1) first; apply's scope IS the saved plans"; exit 1; }

drift: ## Fetch + transform + report config diff (TENANT=<label> [RESOURCE="<type|product> ..."]; real creds via env)
	@test -n "$(TENANT)" || { echo "usage: make drift TENANT=<label> [RESOURCE=\"<type|product> ...\"]"; exit 2; }
	@echo "$(TENANT)" | grep -qE '^[A-Za-z0-9_.-]+$$' || { echo "error: TENANT must match [A-Za-z0-9_.-]+ (got '$(TENANT)')"; exit 2; }
	$(MAKE) fetch TENANT=$(TENANT) $(if $(RESOURCE),RESOURCE="$(RESOURCE)")
	$(MAKE) transform IN=pulls/$(TENANT) TENANT=$(TENANT)
	@if [ -n "$$(git status --porcelain config/$(TENANT) imports/$(TENANT) 2>/dev/null)" ]; then \
		echo ""; echo "DRIFT DETECTED (tenant differs from committed config):"; \
		git status --porcelain config/$(TENANT) imports/$(TENANT); \
		git --no-pager diff --stat config/$(TENANT) 2>/dev/null; \
		exit 3; \
	else \
		echo "no drift: tenant matches committed config"; \
	fi

check-envs: ## Regenerate committed tenants' env roots and fail on drift
	@set -e; regenerated=0; for d in envs/*/; do \
		test -d "$$d" || continue; \
		t=$$(basename "$$d"); \
		$(PYTHON) -m tools.gen_env "$$t" > /dev/null; \
		regenerated=$$((regenerated+1)); \
	done; \
	test $$regenerated -gt 0 || { echo "error: no tenants regenerated — envs/ is empty or missing; nothing to check (expected committed tenant roots under envs/)"; exit 1; }
	@test -z "$$(git status --porcelain -- envs)" || { \
		echo ""; echo "envs/ drifted from the generator output:"; \
		git status --porcelain -- envs; \
		echo "Run make gen-env for each tenant and commit."; exit 1; }

demo: ## Materialize the demo tenant from the public demo dataset (config/demo + imports/demo)
	@set -e; materialized=0; for rt in $$($(PYTHON) -c "from tools.registry import generated_types; print('\n'.join(generated_types()))"); do \
		f="tools/tests/fixtures/demo/$$rt.json"; \
		test -f "$$f" || { echo "missing $$f"; exit 1; }; \
		$(PYTHON) -m tools.transform "$$rt" "$$f" demo; \
		materialized=$$((materialized+1)); \
	done; \
	test $$materialized -gt 0 || { echo "error: no resources materialized — generated_types() returned nothing (registry parse error?); fix tools/registry.json"; exit 1; }

check-demo: ## Fail if the committed demo tenant drifts from the pipeline output
	$(MAKE) demo > /dev/null 2>&1
	@test -z "$$(git status --porcelain -- config/demo imports/demo)" || { \
		echo ""; echo "demo tenant drifted from pipeline output over the demo dataset:"; \
		git status --porcelain -- config/demo imports/demo; \
		echo "Run 'make demo' and commit (or fix the regression it reveals)."; exit 1; }

lint: ## Semantic config lint — pasted chars, URL/IP syntax, set duplicates, order collisions, category shadowing (TENANT=<label>)
	@test -n "$(TENANT)" || { echo "usage: make lint TENANT=<label>"; exit 2; }
	@echo "$(TENANT)" | grep -qE '^[A-Za-z0-9_.-]+$$' || { echo "error: TENANT must match [A-Za-z0-9_.-]+ (got '$(TENANT)')"; exit 2; }
	$(PYTHON) -m tools.lint "$(TENANT)"

fmt-config: ## Rewrite a tenant's config files in canonical transform form (TENANT=<label>)
	@test -n "$(TENANT)" || { echo "usage: make fmt-config TENANT=<label>"; exit 2; }
	@echo "$(TENANT)" | grep -qE '^[A-Za-z0-9_.-]+$$' || { echo "error: TENANT must match [A-Za-z0-9_.-]+ (got '$(TENANT)')"; exit 2; }
	$(PYTHON) -m tools.fmt_config "$(TENANT)"

typecheck: ## Type-check a tenant's config against the provider schemas (stdlib; TENANT=<label>)
	@test -n "$(TENANT)" || { echo "usage: make typecheck TENANT=<label>"; exit 2; }
	@echo "$(TENANT)" | grep -qE '^[A-Za-z0-9_.-]+$$' || { echo "error: TENANT must match [A-Za-z0-9_.-]+ (got '$(TENANT)')"; exit 2; }
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
	@set -e; for d in modules/*/; do \
		rt=$$(basename "$$d"); \
		mkdir -p "tools/tests/fixtures/gen/$$rt"; \
		cp "$$d/variables.tf" "$$d/main.tf" "$$d/outputs.tf" "$$d/versions.tf" \
			"tools/tests/fixtures/gen/$$rt/"; \
	done

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
