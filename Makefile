PYTHON ?= python3
TF     ?= terraform

# Scope glob for the per-root targets (plan/apply/assert-clean/plan-report/
# clean-plans/stage-imports/unstage-imports/test-modules/test-envs/
# validate-imports): a resource type, a glob
# (zia_*), or a SINGLE product token (zia|zpa|zcc) which expands to
# <product>_*. Multi-selector scoping ("zia zpa") is fetch/drift-only —
# the python side expands those. A multi-token RESOURCE here is a loud
# error, NOT a silent mis-scope: the literal multi-word string would
# shell-split inside the per-root globs (only the last token matches, the
# rest become no-ops), so a multi-type bootstrap scope passed to plan/
# stage-imports adopts the wrong set without saying so. Loop the per-root
# target once per type instead (RESOURCE=<type>). Only SCOPE_GLOB is
# guarded, so fetch/transform (which DO take multi-token) are unaffected.
SCOPE_GLOB = $(if $(word 2,$(RESOURCE)),$(error RESOURCE takes a SINGLE selector for per-root targets (plan/apply/stage-imports/assert-clean/...) — got "$(RESOURCE)". Use one resource type, one glob (zia_*), or one product token (zia|zpa|zcc); for a multi-type scope, loop the target once per type. Multi-token RESOURCE is fetch/drift-only.),$(if $(RESOURCE),$(if $(filter zia zpa zcc,$(RESOURCE)),$(RESOURCE)_*,$(RESOURCE)),*))

# Roots the all-tenant sweeps walk: template envs/ always, plus $(OVERLAY)/envs
# only when an overlay is set. At overlay="." filter-out collapses to one token
# (no double-scan). When TENANT is given, targets resolve that tenant's single
# root instead (see envs-dir).
ENV_ROOTS = envs $(if $(filter-out .,$(OVERLAY)),$(OVERLAY)/envs)

.PHONY: help env print-overlay install-tf bump-check mine issue-watch triage surface contract-facts headroom-report adoption-check plan-checks shape plan-report clean clean-plans unlock forget stage-imports unstage-imports import-one statefill lock test test-floor validate schemas generate gen-env transform fetch fetch-diag explain update-goldens update-demo-goldens test-modules test-envs validate-imports plan plan-changed drift-report plan-summary-line assert-clean apply drift check-envs validate-config demo check-demo lint lint-pipelines fmt-config typecheck refresh-gates conformance find-key url-add url-rm domain-add domain-rm keyword-add keyword-rm iprange-add iprange-rm locip-add locip-rm rule-enable rule-disable segment-enable segment-disable op-intake op-resolve op-validate op-status gate check-imports

# Company/deployment extensions: a private repo adds its own targets and
# variable overrides in local.mk — NEVER by editing this file, which is
# template-owned and overwritten on template updates. local.mk is not
# shipped by the template and is yours to commit privately.
#
# Overlay root from deployment.json (default "." => everything at repo root, the
# zero-change default). A PRESENT-but-malformed deployment.json yields empty
# output + a non-zero resolver exit; we fail loud rather than silently degrade.
OVERLAY := $(shell $(PYTHON) -m tools.deployment overlay 2>/dev/null)
ifeq ($(strip $(OVERLAY)),)
$(error deployment.json is present but malformed — the overlay resolver could not read it. Fix or remove deployment.json.)
endif
-include $(OVERLAY)/local.mk

# Deployment data/config: a deployment commits its own config (deployment.json,
# copied from deployment.example.json) plus a private overlay directory it names.
# Both are absent from the template upstream, so a sync never conflicts; the
# gates are path-scoped (modules/, config/, envs/, schemas/) so a committable
# overlay never trips one. deployment.json is plain JSON -- read it directly
# where you need it, and wire any overlay-reading make targets in local.mk.
# Sensitive data (real pulls, secrets) stays in the gitignored pulls/.
# See docs/extending.md.

##@ Toolchain & provider intel

help: ## List available targets, grouped (annotated local.mk targets appear too)
	@awk 'BEGIN {FS = ":.*?## "} /^##@ / {printf "\n%s\n", substr($$0, 5)} /^[a-zA-Z_-]+:.*?## / {printf "  %-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

env: ## Print toolchain versions (diagnostic)
	@uname -sm
	@$(PYTHON) --version 2>&1 || echo "python3: not found"
	@$(MAKE) --version 2>/dev/null | head -1
	@$(TF) version 2>/dev/null | head -1 || echo "terraform: not found"
	@docker --version 2>/dev/null || echo "docker: not found"

print-overlay: ## Echo the resolved overlay root (".", or the deployment.json overlay dir)
	@echo "$(OVERLAY)"

install-tf: ## Download+checksum-verify a pinned terraform (VERSION=<v> [DEST=bin]); then PATH it or pass TF=bin/terraform
	@test -n "$(VERSION)" || { echo "usage: make install-tf VERSION=1.15.4 [DEST=bin]"; exit 2; }
	$(PYTHON) -m tools.install_tf "$(VERSION)" "$(or $(DEST),bin)"

bump-check: ## Check pinned providers + terraform for newer releases (tool exits 4 on updates; make flattens to 2 — a red scheduled run IS the notification)
	TF="$(TF)" $(PYTHON) -m tools.bump_check

mine: ## Mine pinned provider Go source for quirks vs override coverage (tool exits 4 on NEW missing; UPDATE_BASELINE=1 blesses current findings; MINE_VERBOSE=1 also prints info-class DiffSuppress/enum findings; needs network — see tools/MINING.md)
	$(PYTHON) -m tools.mine

issue-watch: ## Watch provider issue trackers for problems with OUR resources (exits 4 on NEW items; UPDATE_BASELINE=1 blesses after triage; needs network — other operators hit problems before we do)
	$(PYTHON) -m tools.issue_watch

surface: ## Sweep the ENTIRE SDK<->terraform surface with synthetic maximal items — no tenant data ([APPLY=1]; exit 4 = paths need eyes; run at every provider/SDK bump; needs network)
	$(PYTHON) -m tools.surface

contract-facts: ## Summarize an automate divergences/rosetta JSON against our registry/overrides (FILE=<*-divergences.json|rosetta.json> [RESOURCE=<type|product|glob> ...])
	@test -n "$(FILE)" || { echo "usage: make contract-facts FILE=<*-divergences.json|rosetta.json> [RESOURCE=<type|product|glob>]"; exit 2; }
	$(PYTHON) -m tools.contract_facts "$(FILE)" $(RESOURCE)

headroom-report: ## Provider schema vs adoption matrix ([RESOURCE=<type|product|glob> ...])
	$(PYTHON) -m tools.headroom_report $(RESOURCE)

adoption-check: ## Targeted live acceptance: fetch + known-hold DROPS_CHECK + scoped env/import validation (TENANT=<label> RESOURCE="<type|product> ...")
	@test -n "$(TENANT)" -a -n "$(RESOURCE)" || { echo "usage: make adoption-check TENANT=<label> RESOURCE=\"<type|product> ...\""; exit 2; }
	@echo "$(TENANT)" | grep -qE '^[A-Za-z0-9_.-]+$$' || { echo "error: TENANT must match [A-Za-z0-9_.-]+ (got '$(TENANT)')"; exit 2; }
	$(MAKE) fetch TENANT=$(TENANT) RESOURCE="$(RESOURCE)"
	$(PYTHON) -m tools.adoption_check "$(TENANT)" $(RESOURCE)
	$(MAKE) gen-env TENANT=$(TENANT) RESOURCE="$(RESOURCE)" $(if $(BACKEND),BACKEND=$(BACKEND))
	@set -e; for rt in $$($(PYTHON) -m tools.adoption_check --resources $(RESOURCE)); do \
		$(MAKE) validate-imports TENANT=$(TENANT) RESOURCE=$$rt; \
	done

triage: ## Classify unacknowledged drop-report fields (IN=pulls/<tenant> [APPLY=1 writes safe classes to acknowledged_drops]; exit 4 = SYNONYM/UNKNOWN paths need eyes; SDK lane needs network)
	@test -n "$(IN)" || { echo "usage: make triage IN=pulls/<tenant> [APPLY=1]"; exit 2; }
	$(PYTHON) -m tools.triage "$(IN)"

plan-checks: ## Policy gates over a plan's NEW additions (FILE=plan.json TENANT=<label> [SSL_EXEMPT_CATEGORY=<config key>]) — URL category shadowing/SSL bypass + location IP overlap; exit 1 = violations
	@test -n "$(FILE)" -a -n "$(TENANT)" || { echo "usage: make plan-checks FILE=plan.json TENANT=<label> [SSL_EXEMPT_CATEGORY=<config key>]"; exit 2; }
	$(PYTHON) -m tools.plan_checks "$(FILE)" "$(TENANT)"

shape: ## Sanitized structural digest of a plan JSON / config / pull (FILE=<path> [ONLY=<resource type>]) — values become tokens; output is safe to relay out of restricted environments
	@test -n "$(FILE)" || { echo "usage: make shape FILE=plan.json [ONLY=zpa_policy_access_rule]"; exit 2; }
	$(PYTHON) -m tools.shape "$(FILE)" $(ONLY)

##@ Tests & template gates

test: ## Run Python unit tests with the local interpreter
	$(PYTHON) -m unittest discover -s tools/tests -t . -v

test-floor: ## Run unit tests under Python 3.6 in Docker (optional dev check; needs docker)
	docker run --rm -v "$$(pwd)":/repo -w /repo python:3.6.8-slim \
		python -m unittest discover -s tools/tests -t . -v

validate: ## Terraform formatting checks
	$(TF) fmt -check -recursive

##@ Generation chain

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

gen-env: ## Generate env roots for a tenant (TENANT=<label> [BACKEND=azurerm] [RESOURCE="<type|product> ..."])
	@test -n "$(TENANT)" || { echo "usage: make gen-env TENANT=<label> [BACKEND=azurerm] [RESOURCE=\"<type|product> ...\"]"; exit 2; }
	@echo "$(TENANT)" | grep -qE '^[A-Za-z0-9_.-]+$$' || { echo "error: TENANT must match [A-Za-z0-9_.-]+ (got '$(TENANT)')"; exit 2; }
	$(PYTHON) -m tools.gen_env "$(TENANT)" $(if $(BACKEND),--backend "$(BACKEND)") $(RESOURCE)

transform: ## Transform pulled API JSON into tfvars + imports (IN=<dir> TENANT=<name> [RESOURCE="<type|product> ..."])
	@test -n "$(IN)" -a -n "$(TENANT)" || { echo "usage: make transform IN=pulls/<tenant> TENANT=<tenant> [RESOURCE=\"<type|product> ...\"]"; exit 2; }
	@echo "$(TENANT)" | grep -qE '^[A-Za-z0-9_.-]+$$' || { echo "error: TENANT must match [A-Za-z0-9_.-]+ (got '$(TENANT)')"; exit 2; }
	@failed=""; sel="$(RESOURCE)"; \
	for rt in $$($(PYTHON) -c "from tools.registry import generated_types; print('\n'.join(generated_types()))"); do \
		if [ -n "$$sel" ]; then \
			match=""; \
			for tok in $$sel; do \
				case "$$tok" in \
					zia|zpa|zcc) case "$$rt" in "$$tok"_*) match=1 ;; esac ;; \
					*) if [ "$$rt" = "$$tok" ]; then match=1; fi ;; \
				esac; \
			done; \
			[ -n "$$match" ] || continue; \
		fi; \
		src=$$($(PYTHON) -c "from tools.registry import derive_entry; d=derive_entry('$$rt'); print(d['from'] if d else '$$rt')"); \
		if [ -f "$(IN)/$$src.json" ]; then \
			$(PYTHON) -m tools.transform "$$rt" "$(IN)/$$src.json" "$(TENANT)" || failed="$$failed $$rt"; \
		else \
			echo "skip $$rt (no $(IN)/$$src.json)"; \
		fi; \
	done; \
	test -z "$$failed" || { echo ""; echo "transform FAILED for:$$failed"; \
		echo "(fix the override map per the error above; successful outputs are already written)"; exit 1; }

fetch: ## Pull API JSON into pulls/<tenant> (TENANT=<name> [RESOURCE="<type|product> ..."]; products zia/zpa/zcc expand; real creds via env — trusted env only)
	@test -n "$(TENANT)" || { echo "usage: make fetch TENANT=<tenant> [RESOURCE=<type>] (with ZSCALER_*/ZIA_*/ZPA_* env set)"; exit 2; }
	@echo "$(TENANT)" | grep -qE '^[A-Za-z0-9_.-]+$$' || { echo "error: TENANT must match [A-Za-z0-9_.-]+ (got '$(TENANT)')"; exit 2; }
	$(PYTHON) -m tools.fetch "$(TENANT)" $(RESOURCE)

fetch-diag: ## Probe TLS to the fetcher's hosts under system trust and +bundle
	$(PYTHON) -m tools.fetch --diag

explain: ## Print config with ID references resolved (TENANT=<label> [RESOURCE="<type|product|glob> ..."])
	@test -n "$(TENANT)" || { echo "usage: make explain TENANT=<label> [RESOURCE=\"<type|product|glob> ...\"]"; exit 2; }
	@echo "$(TENANT)" | grep -qE '^[A-Za-z0-9_.-]+$$' || { echo "error: TENANT must match [A-Za-z0-9_.-]+ (got '$(TENANT)')"; exit 2; }
	$(PYTHON) -m tools.lookup explain "$(TENANT)" $(RESOURCE)

##@ Tenant gates

test-modules: ## Run mock-provider terraform tests across generated modules ([RESOURCE=<type>] scopes to one)
	@set -e; matched=0; for d in modules/$(SCOPE_GLOB)/; do \
		[ -d "$$d" ] || continue; \
		matched=$$((matched+1)); \
		echo "== $$d"; \
		$(TF) -chdir=$$d init -backend=false -input=false > /dev/null; \
		$(TF) -chdir=$$d test; \
		rm -rf $$d/.terraform $$d/.terraform.lock.hcl; \
	done; \
	test $$matched -gt 0 || { echo "error: no module matched RESOURCE='$(RESOURCE)' under modules/ — typo, wrong product token, or run make generate first"; exit 1; }

test-envs: ## Run mock-provider smoke tests across a tenant's env roots (TENANT=<label> [RESOURCE=<type>] scopes to one)
	@test -n "$(TENANT)" || { echo "usage: make test-envs TENANT=<label> [RESOURCE=<type>]"; exit 2; }
	@echo "$(TENANT)" | grep -qE '^[A-Za-z0-9_.-]+$$' || { echo "error: TENANT must match [A-Za-z0-9_.-]+ (got '$(TENANT)')"; exit 2; }
	@set -e; matched=0; for d in envs/$(TENANT)/$(SCOPE_GLOB)/; do \
		[ -d "$$d" ] || continue; \
		matched=$$((matched+1)); \
		echo "== $$d"; \
		$(TF) -chdir=$$d init -backend=false -input=false > /dev/null; \
		$(TF) -chdir=$$d test; \
	done; \
	test $$matched -gt 0 || { echo "error: no env root matched RESOURCE='$(RESOURCE)' under envs/$(TENANT)/ — typo, wrong product token, or run make gen-env TENANT=$(TENANT) first"; exit 1; }

validate-imports: ## Validate fixture import addresses against a tenant's roots (TENANT=<label> [RESOURCE=<type>] scopes to one)
	@test -n "$(TENANT)" || { echo "usage: make validate-imports TENANT=<label> [RESOURCE=<type>]"; exit 2; }
	@echo "$(TENANT)" | grep -qE '^[A-Za-z0-9_.-]+$$' || { echo "error: TENANT must match [A-Za-z0-9_.-]+ (got '$(TENANT)')"; exit 2; }
	@set -e; matched=0; for d in envs/$(TENANT)/$(SCOPE_GLOB)/; do \
		[ -d "$$d" ] || continue; \
		matched=$$((matched+1)); \
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
	done; \
	test $$matched -gt 0 || { echo "error: no env root matched RESOURCE='$(RESOURCE)' under envs/$(TENANT)/ — typo, wrong product token, or run make gen-env TENANT=$(TENANT) first"; exit 1; }

##@ Plan / apply / state ops

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

plan: ## Terraform plan for a tenant's roots (TENANT=<label> [RESOURCE=<type>] [IMPORTS_ONLY=1] [BACKEND_CONFIG=<file>]; real creds via env)
	@test -n "$(TENANT)" || { echo "usage: make plan TENANT=<label> [RESOURCE=<type>] [IMPORTS_ONLY=1] [BACKEND_CONFIG=<file>]"; exit 2; }
	@echo "$(TENANT)" | grep -qE '^[A-Za-z0-9_.-]+$$' || { echo "error: TENANT must match [A-Za-z0-9_.-]+ (got '$(TENANT)')"; exit 2; }
	@set -e; planned=0; derived=""; \
	if [ -n "$(IMPORTS_ONLY)" ]; then derived=" $$($(PYTHON) -c 'from tools.registry import derived_types; print(" ".join(derived_types()))') "; fi; \
	for d in envs/$(TENANT)/$(SCOPE_GLOB)/; do \
		test -d "$$d" || continue; \
		rt=$$(basename $$d); \
		if [ -n "$$derived" ]; then case "$$derived" in *" $$rt "*) \
			echo "skip $$rt (IMPORTS_ONLY: derived/non-importable — created by normal delivery, not the imports-only bootstrap)"; continue ;; esac; fi; \
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
	@removed=0; for base in $(ENV_ROOTS); do for f in $$base/*/*/*_imports.tf $$base/*/*/*_moves.tf $$base/*/*/.state-list.tmp; do \
		test -f "$$f" || continue; \
		rm -f "$$f"; removed=$$((removed+1)); \
	done; done; \
	rm -rf reports .plan-changed.tmp; \
	echo "clean: removed staged copies ($$removed), saved plans, reports/, temp files"

clean-plans: ## Delete saved tfplan artifacts ([TENANT=<label>] [RESOURCE=<type>]) — run before any fresh plan set; stale plans from a failed/cancelled run otherwise ride into the next apply
	@removed=0; roots="$(if $(TENANT),$(shell $(PYTHON) -m tools.deployment envs-dir $(TENANT)),$(ENV_ROOTS))"; \
	for base in $$roots; do for d in $$base/$(if $(TENANT),,*/)$(SCOPE_GLOB)/; do \
		test -f "$$d/tfplan" || continue; \
		rm -f "$$d/tfplan"; echo "removed $$d""tfplan"; removed=$$((removed+1)); \
	done; done; \
	echo "$$removed stale plan(s) removed"

plan-changed: ## Plan only the (tenant, resource) pairs changed vs BASE (default origin/main; [TENANT=<label>] scopes to one tenant); SAVE/BACKEND_CONFIG pass through
	@$(MAKE) clean-plans > /dev/null
	@set -e; $(PYTHON) -m tools.changed "$(or $(BASE),origin/main)" $(if $(TENANT),--tenant $(TENANT)) > .plan-changed.tmp; \
	if ! [ -s .plan-changed.tmp ]; then rm -f .plan-changed.tmp; echo "nothing to plan — no plannable changes vs $(or $(BASE),origin/main)$(if $(TENANT), for tenant $(TENANT))"; exit 0; fi; \
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
	roots="$(if $(TENANT),$(shell $(PYTHON) -m tools.deployment envs-dir $(TENANT)),$(ENV_ROOTS))"; \
	for base in $$roots; do for d in $$base/$(if $(TENANT),,*/)$(SCOPE_GLOB)/; do \
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
	done; done; \
	test $$found -gt 0 || { rm -f "$$body" "$$rows"; echo "error: no saved plans — run make plan-changed SAVE=1 (or make plan SAVE=1) first"; exit 1; }; \
	{ printf '## Terraform plan\n\n'; \
	  if [ "$$destroys_total" -gt 0 ]; then \
		printf '> :warning: **%d DESTROY(S) in this plan set — read those roots first. Applies refuse destroys without ALLOW_DESTROY=1.**\n\n' "$$destroys_total"; \
	  fi; \
	  printf '| root | import | add | change | destroy |\n|---|---|---|---|---|\n'; \
	  cat "$$rows"; printf '\n'; cat "$$body"; } > "$$out"; \
	rm -f "$$body" "$$rows"; \
	echo "wrote $$out ($$found plan(s), $$destroys_total destroy(s))"

plan-summary-line: ## Print ONE compact line (roots + add/change/destroy totals) across saved plans — feed it into an approval prompt variable ([TENANT=<label>] [RESOURCE=<type>])
	@set -e; roots=0; sa=0; sc=0; sd=0; \
	bases="$(if $(TENANT),$(shell $(PYTHON) -m tools.deployment envs-dir $(TENANT)),$(ENV_ROOTS))"; \
	for base in $$bases; do for d in $$base/$(if $(TENANT),,*/)$(SCOPE_GLOB)/; do \
		test -f "$$d/tfplan" || continue; \
		roots=$$((roots+1)); \
		c=$$($(TF) -chdir="$$d" show -json tfplan | $(PYTHON) -m tools.plan_summary --counts); \
		set -- $$c; sa=$$((sa+$$2)); sc=$$((sc+$$3)); sd=$$((sd+$$4)); \
	done; done; \
	test $$roots -gt 0 || { echo "no changed roots"; exit 0; }; \
	if [ "$$sd" -gt 0 ]; then dsfx=" | $$sd DESTROY"; else dsfx=""; fi; \
	printf '%d root(s): +%d ~%d -%d%s\n' "$$roots" "$$sa" "$$sc" "$$sd" "$$dsfx"

assert-clean: ## Exit 0 only when every saved plan is no-op (imports allowed) — the drift-PR merge-readiness check ([TENANT=<label>] [RESOURCE=<type>])
	@set -e; checked=0; dirty=0; \
	roots="$(if $(TENANT),$(shell $(PYTHON) -m tools.deployment envs-dir $(TENANT)),$(ENV_ROOTS))"; \
	for base in $$roots; do for d in $$base/$(if $(TENANT),,*/)$(SCOPE_GLOB)/; do \
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
	done; done; \
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

import-one: ## CLI-import ONE address into state (TENANT= RESOURCE= KEY= IMPORT_ID= [BACKEND_CONFIG=]) — the statefill pre-import for a provider-unreadable required field; reads the API (GET only), writes STATE, never the tenant. Needs provider creds (and HTTPS_PROXY on proxied egress, same as fetch)
	@test -n "$(TENANT)" -a -n "$(RESOURCE)" -a -n "$(KEY)" -a -n "$(IMPORT_ID)" || { echo "usage: make import-one TENANT=<label> RESOURCE=<type> KEY=<config key> IMPORT_ID=<api id> [BACKEND_CONFIG=<file>]"; exit 2; }
	@echo "$(TENANT)" | grep -qE '^[A-Za-z0-9_.-]+$$' || { echo "error: TENANT must match [A-Za-z0-9_.-]+ (got '$(TENANT)')"; exit 2; }
	@test -d "envs/$(TENANT)/$(RESOURCE)" || { echo "error: envs/$(TENANT)/$(RESOURCE) is not an env root — RESOURCE must be ONE concrete type"; exit 2; }
	@vf="$(abspath config/$(TENANT))/$(RESOURCE).auto.tfvars.json"; \
	test -f "$$vf" || { echo "error: $$vf not found — run make transform first (import evaluates config, which needs the var-file: it lives in config/, not the env root, so it is NOT auto-loaded)"; exit 1; }; \
	if grep -q '^  backend "' "envs/$(TENANT)/$(RESOURCE)/main.tf" && [ -z "$(BACKEND_CONFIG)" ]; then \
		echo "error: $(RESOURCE) declares a remote backend; run with BACKEND_CONFIG=<file>"; exit 1; fi; \
	$(TF) -chdir="envs/$(TENANT)/$(RESOURCE)" init -input=false $(if $(BACKEND_CONFIG),-reconfigure -backend-config="$(abspath $(BACKEND_CONFIG))" -backend-config="key=$(TENANT)/$(RESOURCE).tfstate") > /dev/null \
		|| { echo "error: terraform init failed for envs/$(TENANT)/$(RESOURCE) — backend creds, or a provider lock missing this agent's OS/arch (re-run make lock)"; exit 1; }; \
	addr='module.$(RESOURCE).$(RESOURCE).this["$(KEY)"]'; \
	out=$$($(TF) -chdir="envs/$(TENANT)/$(RESOURCE)" import -input=false -var-file="$$vf" "$$addr" '$(IMPORT_ID)' 2>&1); rc=$$?; \
	printf '%s\n' "$$out"; \
	if [ $$rc -ne 0 ] && printf '%s' "$$out" | grep -q 'already managed by Terraform'; then \
		echo "note: $(KEY) is already in state (idempotent import) — nothing to do"; rc=0; \
	fi; \
	if [ $$rc -ne 0 ]; then \
		echo "error: import of $(KEY) FAILED (exit $$rc) — do NOT run statefill; the item is not in state. A provider crash here ('Plugin did not respond' at ConfigureProvider) is an auth/client failure, not a config problem: capture the panic/crash.log and check the corp root CA is in the SYSTEM trust store (the Go provider ignores REQUESTS_CA_BUNDLE) and HTTPS_PROXY is set"; \
	fi; \
	exit $$rc

statefill: ## Fill ONE config-carried field into STATE, zero tenant writes (TENANT= RESOURCE= KEY= FIELD= [BACKEND_CONFIG=]) — for provider reads that cannot return a required field (ISOLATE cbi_profile class); RE-FETCH+RE-TRANSFORM FIRST (the fill copies committed config); preview by default, STATE_FILL=1 pushes
	@test -n "$(TENANT)" -a -n "$(RESOURCE)" -a -n "$(KEY)" -a -n "$(FIELD)" || { echo "usage: make statefill TENANT=<label> RESOURCE=<type> KEY=<config key> FIELD=<field> [BACKEND_CONFIG=<file>] [STATE_FILL=1]"; exit 2; }
	@test -d "envs/$(TENANT)/$(RESOURCE)" || { echo "error: envs/$(TENANT)/$(RESOURCE) is not an env root — RESOURCE must be ONE concrete type"; exit 2; }
	$(TF) -chdir=envs/$(TENANT)/$(RESOURCE) init -input=false $(if $(BACKEND_CONFIG),-reconfigure -backend-config="$(abspath $(BACKEND_CONFIG))" -backend-config="key=$(TENANT)/$(RESOURCE).tfstate") > /dev/null
	@set -e; tmp=$$(mktemp -d); trap 'rm -rf "$$tmp"' EXIT; \
	$(TF) -chdir=envs/$(TENANT)/$(RESOURCE) state pull > "$$tmp/pulled.json"; \
	$(PYTHON) -m tools.statefill "$$tmp/pulled.json" '$(RESOURCE)' '$(KEY)' '$(FIELD)' '$(TENANT)' > "$$tmp/filled.json"; \
	if [ "$(STATE_FILL)" = "1" ]; then \
		$(TF) -chdir=envs/$(TENANT)/$(RESOURCE) state push "$$tmp/filled.json"; \
		echo "state filled — next: make stage-imports TENANT=$(TENANT) RESOURCE=$(RESOURCE) STATE_AWARE=1, then re-plan; the plan must now be imports-only/no-op"; \
	else \
		echo "PREVIEW ONLY (summary above) — confirm config is FRESH (re-fetch + re-transform first; the fill copies committed config), then re-run with STATE_FILL=1"; \
	fi

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
	@set -e; applied=0; \
	roots="$(if $(TENANT),$(shell $(PYTHON) -m tools.deployment envs-dir $(TENANT)),$(ENV_ROOTS))"; \
	for base in $$roots; do for d in $$base/$(if $(TENANT),,*/)$(SCOPE_GLOB)/; do \
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
	done; done; \
	test $$applied -gt 0 || { echo "error: no saved plans found — run 'make plan SAVE=1 ...' (or plan-changed SAVE=1) first; apply's scope IS the saved plans"; exit 1; }

drift: ## Fetch + transform + report config diff (TENANT=<label> [RESOURCE="<type|product> ..."]; real creds via env)
	@test -n "$(TENANT)" || { echo "usage: make drift TENANT=<label> [RESOURCE=\"<type|product> ...\"]"; exit 2; }
	@echo "$(TENANT)" | grep -qE '^[A-Za-z0-9_.-]+$$' || { echo "error: TENANT must match [A-Za-z0-9_.-]+ (got '$(TENANT)')"; exit 2; }
	$(MAKE) fetch TENANT=$(TENANT) $(if $(RESOURCE),RESOURCE="$(RESOURCE)")
	$(MAKE) transform IN=pulls/$(TENANT) TENANT=$(TENANT) $(if $(RESOURCE),RESOURCE="$(RESOURCE)")
	@if [ -n "$$(git status --porcelain config/$(TENANT) imports/$(TENANT) 2>/dev/null)" ]; then \
		echo ""; echo "DRIFT DETECTED (tenant differs from committed config):"; \
		git status --porcelain config/$(TENANT) imports/$(TENANT); \
		git --no-pager diff --stat config/$(TENANT) 2>/dev/null; \
		exit 3; \
	else \
		echo "no drift: tenant matches committed config"; \
	fi

##@ Consistency & demo

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
		src=$$($(PYTHON) -c "from tools.registry import derive_entry; d=derive_entry('$$rt'); print(d['from'] if d else '$$rt')"); \
		f="tools/tests/fixtures/demo/$$src.json"; \
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

##@ Config gates

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

# Pipeline YAML is adapted per-shop and does not update on pull — gate
# logic must live HERE so a repo pull is enough to change gate behavior.
refresh-gates: ## Gates for freshly-FETCHED config: advisory lint + strict typecheck (TENANT=<label>)
	@test -n "$(TENANT)" || { echo "usage: make refresh-gates TENANT=<label>"; exit 2; }
	@echo "$(TENANT)" | grep -qE '^[A-Za-z0-9_.-]+$$' || { echo "error: TENANT must match [A-Za-z0-9_.-]+ (got '$(TENANT)')"; exit 2; }
	LINT_ADVISORY=1 $(PYTHON) -m tools.lint "$(TENANT)"
	$(PYTHON) -m tools.typecheck "$(TENANT)"

conformance: ## Schema-driven adversarial conformance report (synthesize -> transform -> typecheck) for every registry resource
	$(PYTHON) -m tools.conformance

# Lives HERE, not in adapted pipeline YAML, so a repo pull updates the gate
# (same reason as refresh-gates). Run it deployment-side over the operative
# pipelines: make lint-pipelines DIR=<your pipelines dir>.
lint-pipelines: ## Cross-pipeline consistency lint — terraform-version drift, hand-rolled auth, config in step env, backend.conf strategy ([DIR=pipelines | FILES="a.yml b.yml"] [TF_VERSION=x.y.z] [STRICT=1])
	$(PYTHON) -m tools.lint_pipelines $(if $(FILES),$(FILES),$(if $(DIR),--dir $(DIR),)) $(if $(TF_VERSION),--tf-version $(TF_VERSION),) $(if $(STRICT),--strict,)

validate-config: ## Validate config/ against generated JSON Schemas (dev-only; jsonschema via python or uv)
	@if $(PYTHON) -c "import jsonschema" 2>/dev/null; then \
		$(PYTHON) -m tools.validate_config; \
	elif command -v uv >/dev/null 2>&1; then \
		uv run --quiet --with jsonschema python -m tools.validate_config; \
	else \
		echo "WARNING: no python 'jsonschema' and no uv - skipping config validation"; \
		echo "(dev-only check; never required in restricted environments)"; \
	fi

##@ BAU config edits

# Deterministic, idempotent, allowlisted single-edits for the high-churn tasks
# (see docs/workflows/operate-change.md). They edit COMMITTED config only — run
# the gates (typecheck/lint/plan-changed) and raise a PR; never apply directly.

find-key: ## Resolve a display name to its config key(s) (TENANT=<label> TYPE=<resource_type> NAME="<display name>")
	@test -n "$(TENANT)" -a -n "$(TYPE)" -a -n "$(NAME)" || { echo "usage: make find-key TENANT=<label> TYPE=<resource_type> NAME=\"<display name>\""; exit 2; }
	@echo "$(TENANT)" | grep -qE '^[A-Za-z0-9_.-]+$$' || { echo "error: TENANT must match [A-Za-z0-9_.-]+"; exit 2; }
	$(PYTHON) -m tools.operate resolve "$(TENANT)" "$(TYPE)" "$(NAME)"

url-add: ## Add a URL to a custom URL category (TENANT=<label> CATEGORY=<config-key> URL=<url>)
	@test -n "$(TENANT)" -a -n "$(CATEGORY)" -a -n "$(URL)" || { echo "usage: make url-add TENANT=<label> CATEGORY=<config-key> URL=<url>"; exit 2; }
	@echo "$(TENANT)" | grep -qE '^[A-Za-z0-9_.-]+$$' || { echo "error: TENANT must match [A-Za-z0-9_.-]+"; exit 2; }
	$(PYTHON) -m tools.operate add "$(TENANT)" zia_url_categories "$(CATEGORY)" urls "$(URL)"

url-rm: ## Remove a URL from a custom URL category (TENANT=<label> CATEGORY=<config-key> URL=<url>)
	@test -n "$(TENANT)" -a -n "$(CATEGORY)" -a -n "$(URL)" || { echo "usage: make url-rm TENANT=<label> CATEGORY=<config-key> URL=<url>"; exit 2; }
	@echo "$(TENANT)" | grep -qE '^[A-Za-z0-9_.-]+$$' || { echo "error: TENANT must match [A-Za-z0-9_.-]+"; exit 2; }
	$(PYTHON) -m tools.operate remove "$(TENANT)" zia_url_categories "$(CATEGORY)" urls "$(URL)"

domain-add: ## Add a domain to a ZPA app segment (TENANT=<label> SEGMENT=<config-key> DOMAIN=<domain>)
	@test -n "$(TENANT)" -a -n "$(SEGMENT)" -a -n "$(DOMAIN)" || { echo "usage: make domain-add TENANT=<label> SEGMENT=<config-key> DOMAIN=<domain>"; exit 2; }
	@echo "$(TENANT)" | grep -qE '^[A-Za-z0-9_.-]+$$' || { echo "error: TENANT must match [A-Za-z0-9_.-]+"; exit 2; }
	$(PYTHON) -m tools.operate add "$(TENANT)" zpa_application_segment "$(SEGMENT)" domain_names "$(DOMAIN)"

domain-rm: ## Remove a domain from a ZPA app segment (TENANT=<label> SEGMENT=<config-key> DOMAIN=<domain>)
	@test -n "$(TENANT)" -a -n "$(SEGMENT)" -a -n "$(DOMAIN)" || { echo "usage: make domain-rm TENANT=<label> SEGMENT=<config-key> DOMAIN=<domain>"; exit 2; }
	@echo "$(TENANT)" | grep -qE '^[A-Za-z0-9_.-]+$$' || { echo "error: TENANT must match [A-Za-z0-9_.-]+"; exit 2; }
	$(PYTHON) -m tools.operate remove "$(TENANT)" zpa_application_segment "$(SEGMENT)" domain_names "$(DOMAIN)"

keyword-add: ## Add a keyword to a custom URL category (TENANT=<label> CATEGORY=<config-key> KEYWORD=<keyword>)
	@test -n "$(TENANT)" -a -n "$(CATEGORY)" -a -n "$(KEYWORD)" || { echo "usage: make keyword-add TENANT=<label> CATEGORY=<config-key> KEYWORD=<keyword>"; exit 2; }
	@echo "$(TENANT)" | grep -qE '^[A-Za-z0-9_.-]+$$' || { echo "error: TENANT must match [A-Za-z0-9_.-]+"; exit 2; }
	$(PYTHON) -m tools.operate add "$(TENANT)" zia_url_categories "$(CATEGORY)" keywords "$(KEYWORD)"

keyword-rm: ## Remove a keyword from a custom URL category (TENANT=<label> CATEGORY=<config-key> KEYWORD=<keyword>)
	@test -n "$(TENANT)" -a -n "$(CATEGORY)" -a -n "$(KEYWORD)" || { echo "usage: make keyword-rm TENANT=<label> CATEGORY=<config-key> KEYWORD=<keyword>"; exit 2; }
	@echo "$(TENANT)" | grep -qE '^[A-Za-z0-9_.-]+$$' || { echo "error: TENANT must match [A-Za-z0-9_.-]+"; exit 2; }
	$(PYTHON) -m tools.operate remove "$(TENANT)" zia_url_categories "$(CATEGORY)" keywords "$(KEYWORD)"

iprange-add: ## Add an IP/CIDR/range to a custom URL category (TENANT=<label> CATEGORY=<config-key> IPRANGE=<ip|cidr|a-b>)
	@test -n "$(TENANT)" -a -n "$(CATEGORY)" -a -n "$(IPRANGE)" || { echo "usage: make iprange-add TENANT=<label> CATEGORY=<config-key> IPRANGE=<ip|cidr|a-b>"; exit 2; }
	@echo "$(TENANT)" | grep -qE '^[A-Za-z0-9_.-]+$$' || { echo "error: TENANT must match [A-Za-z0-9_.-]+"; exit 2; }
	$(PYTHON) -m tools.operate add "$(TENANT)" zia_url_categories "$(CATEGORY)" ip_ranges "$(IPRANGE)"

iprange-rm: ## Remove an IP/CIDR/range from a custom URL category (TENANT=<label> CATEGORY=<config-key> IPRANGE=<ip|cidr|a-b>)
	@test -n "$(TENANT)" -a -n "$(CATEGORY)" -a -n "$(IPRANGE)" || { echo "usage: make iprange-rm TENANT=<label> CATEGORY=<config-key> IPRANGE=<ip|cidr|a-b>"; exit 2; }
	@echo "$(TENANT)" | grep -qE '^[A-Za-z0-9_.-]+$$' || { echo "error: TENANT must match [A-Za-z0-9_.-]+"; exit 2; }
	$(PYTHON) -m tools.operate remove "$(TENANT)" zia_url_categories "$(CATEGORY)" ip_ranges "$(IPRANGE)"

locip-add: ## Add an IP/CIDR/range to a location's IP addresses (TENANT=<label> LOCATION=<config-key> IPADDR=<ip|cidr|a-b>)
	@test -n "$(TENANT)" -a -n "$(LOCATION)" -a -n "$(IPADDR)" || { echo "usage: make locip-add TENANT=<label> LOCATION=<config-key> IPADDR=<ip|cidr|a-b>"; exit 2; }
	@echo "$(TENANT)" | grep -qE '^[A-Za-z0-9_.-]+$$' || { echo "error: TENANT must match [A-Za-z0-9_.-]+"; exit 2; }
	$(PYTHON) -m tools.operate add "$(TENANT)" zia_location_management "$(LOCATION)" ip_addresses "$(IPADDR)"

locip-rm: ## Remove an IP/CIDR/range from a location's IP addresses (TENANT=<label> LOCATION=<config-key> IPADDR=<ip|cidr|a-b>)
	@test -n "$(TENANT)" -a -n "$(LOCATION)" -a -n "$(IPADDR)" || { echo "usage: make locip-rm TENANT=<label> LOCATION=<config-key> IPADDR=<ip|cidr|a-b>"; exit 2; }
	@echo "$(TENANT)" | grep -qE '^[A-Za-z0-9_.-]+$$' || { echo "error: TENANT must match [A-Za-z0-9_.-]+"; exit 2; }
	$(PYTHON) -m tools.operate remove "$(TENANT)" zia_location_management "$(LOCATION)" ip_addresses "$(IPADDR)"

rule-enable: ## Enable a ZIA rule (TENANT=<label> TYPE=<zia_url_filtering_rules|zia_ssl_inspection_rules|zia_cloud_app_control_rule> RULE=<config-key>)
	@test -n "$(TENANT)" -a -n "$(TYPE)" -a -n "$(RULE)" || { echo "usage: make rule-enable TENANT=<label> TYPE=<resource_type> RULE=<config-key>"; exit 2; }
	@echo "$(TENANT)" | grep -qE '^[A-Za-z0-9_.-]+$$' || { echo "error: TENANT must match [A-Za-z0-9_.-]+"; exit 2; }
	$(PYTHON) -m tools.operate set "$(TENANT)" "$(TYPE)" "$(RULE)" state ENABLED

rule-disable: ## Disable a ZIA rule (TENANT=<label> TYPE=<...> RULE=<config-key>)
	@test -n "$(TENANT)" -a -n "$(TYPE)" -a -n "$(RULE)" || { echo "usage: make rule-disable TENANT=<label> TYPE=<resource_type> RULE=<config-key>"; exit 2; }
	@echo "$(TENANT)" | grep -qE '^[A-Za-z0-9_.-]+$$' || { echo "error: TENANT must match [A-Za-z0-9_.-]+"; exit 2; }
	$(PYTHON) -m tools.operate set "$(TENANT)" "$(TYPE)" "$(RULE)" state DISABLED

segment-enable: ## Enable a ZPA app segment or segment group (TENANT=<label> TYPE=<zpa_application_segment|zpa_segment_group> SEGMENT=<config-key>)
	@test -n "$(TENANT)" -a -n "$(TYPE)" -a -n "$(SEGMENT)" || { echo "usage: make segment-enable TENANT=<label> TYPE=<resource_type> SEGMENT=<config-key>"; exit 2; }
	@echo "$(TENANT)" | grep -qE '^[A-Za-z0-9_.-]+$$' || { echo "error: TENANT must match [A-Za-z0-9_.-]+"; exit 2; }
	$(PYTHON) -m tools.operate set "$(TENANT)" "$(TYPE)" "$(SEGMENT)" enabled true

segment-disable: ## Disable a ZPA app segment or segment group (TENANT=<label> TYPE=<...> SEGMENT=<config-key>)
	@test -n "$(TENANT)" -a -n "$(TYPE)" -a -n "$(SEGMENT)" || { echo "usage: make segment-disable TENANT=<label> TYPE=<resource_type> SEGMENT=<config-key>"; exit 2; }
	@echo "$(TENANT)" | grep -qE '^[A-Za-z0-9_.-]+$$' || { echo "error: TENANT must match [A-Za-z0-9_.-]+"; exit 2; }
	$(PYTHON) -m tools.operate set "$(TENANT)" "$(TYPE)" "$(SEGMENT)" enabled false

##@ Workflow & runtime gates

op-intake: ## Workflow: write the intake artifact (SLUG=<yyyy-mm-dd-slug> TENANT=<label> INTAKE=<path>)
	@test -n "$(SLUG)" -a -n "$(TENANT)" -a -n "$(INTAKE)" || { echo "usage: make op-intake SLUG=<slug> TENANT=<label> INTAKE=<path>"; exit 2; }
	$(PYTHON) -m tools.opgate intake --slug "$(SLUG)" --tenant "$(TENANT)" --intake-json "$(INTAKE)"

op-resolve: ## Workflow GATE G1: resolve targets to config keys, exactly-one-match or blocked (SLUG= TENANT= TARGETS=<path>)
	@test -n "$(SLUG)" -a -n "$(TENANT)" -a -n "$(TARGETS)" || { echo "usage: make op-resolve SLUG=<slug> TENANT=<label> TARGETS=<path>"; exit 2; }
	$(PYTHON) -m tools.opgate resolve --slug "$(SLUG)" --tenant "$(TENANT)" --target-json "$(TARGETS)"

op-validate: ## Workflow GATE G2: run typecheck + lint, write pass|blocked (SLUG= TENANT=)
	@test -n "$(SLUG)" -a -n "$(TENANT)" || { echo "usage: make op-validate SLUG=<slug> TENANT=<label>"; exit 2; }
	$(PYTHON) -m tools.opgate validate --slug "$(SLUG)" --tenant "$(TENANT)"

op-status: ## Workflow: print the latest phase/status/next_step for a ticket (SLUG=)
	@test -n "$(SLUG)" || { echo "usage: make op-status SLUG=<slug>"; exit 2; }
	$(PYTHON) -m tools.opgate status --slug "$(SLUG)"

gate: ## Run a gate target via the runtime and write outputs/gates/<GATE>.status.json (GATE=<target> [ARGS="TENANT=<label> ..."])
	@test -n "$(GATE)" || { echo "usage: make gate GATE=<target> [ARGS=\"TENANT=<label> ...\"]"; exit 2; }
	@test "$(GATE)" != "gate" || { echo "error: GATE=gate would recurse"; exit 2; }
	uv run --project runtime zac-gate "$(GATE)" $(ARGS)

check-imports: ## Enforce the one-way rule: nothing under tools/ imports runtime/
	@! grep -rnE '^[[:space:]]*(import|from)[[:space:]]+zac_runtime\b' tools/ \
		&& echo "ok: tools/ does not import runtime/" \
		|| { echo "VIOLATION: tools/ imports runtime/"; exit 1; }

##@ Template authoring (dev)

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
print('blessed', rt)"; \
	done
