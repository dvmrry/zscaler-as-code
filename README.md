# zscaler-as-code

Schema-driven Terraform boilerplate for managing Zscaler (ZIA + ZPA + ZCC) where
all configuration lives in typed JSON (`.auto.tfvars.json`) and module code
is generated from the provider schema rather than hand-curated.

This is a template: it ships with fictional sample data and contains no
credentials or tenant-specific values. Bring your own tfvars in a private
repo. See `AGENTS.md` for the repo invariants.

## Design constraints

- The provider schema is the single source of truth: modules and JSON
  Schemas are generated, committed, and checked for drift in CI.
- Generated output is deterministic — provider bumps produce reviewable
  git diffs.
- Python tooling is stdlib-only at a Python 3.6 syntax floor so it runs
  in restricted enterprise environments.
- All logic lives in `make` targets; CI pipelines are thin shells.

## Entry points

All workflows are `make` targets — run `make help` to list them (stays
authoritative). Do not invent other invocation paths. For step-by-step
adoption and drift-detection procedures, see `RUNBOOK.md`.

Common ops targets:

| Target | Purpose |
|---|---|
| `make gen-env TENANT=<label>` | Generate Terraform root modules for a tenant |
| `make plan TENANT=<label>` | Terraform plan all roots for a tenant (real creds via env) |
| `make drift TENANT=<label>` | Fetch live config, transform, and report drift vs committed state (exits 3 on drift) |
| `make check-envs` | Regenerate all tenant env roots and fail if any differ from committed |
| `make test-envs TENANT=<label>` | Mock-provider smoke tests across a tenant's env roots |
| `make validate-imports TENANT=<label>` | Validate fixture import addresses against a tenant's roots |
| `make validate-config` | Validate config/ tfvars against generated JSON Schemas (dev-only; skips gracefully if jsonschema is not installed) |

## Layout

    RUNBOOK.md                 adoption and drift-detection procedures
    modules/<resource-type>/   GENERATED Terraform modules — never hand-edit
    schemas/provider/          pinned provider schema dumps (make schemas)
    schemas/tfvars/            generated JSON Schemas for config files
    tools/                     stdlib-only Python (3.6-floor) + overrides
    envs/<tenant>/             root modules, split state
    config/<tenant>/           fictional sample tfvars
    imports/<tenant>/          transform-emitted import blocks

Directories not yet present are created by later build phases.

## CI

`azure-pipelines.example.yml` is a starting point for Azure DevOps: point a
pipeline definition at it (or copy it) and adapt the agent pool and
toolchain setup to your environment. Pipelines stay thin shells — they only
call `make` targets.

## Regenerating provider schemas

`make schemas` runs `terraform providers schema -json` against the pinned
providers in `tools/schema-extract/` and rewrites `schemas/provider/`.
To bump a provider: edit the `version` pin in `tools/schema-extract/main.tf`,
run `terraform -chdir=tools/schema-extract init -upgrade`, then
`make schemas` and review the resulting git diff.

Schema extraction is an authoring step. Everywhere else — consuming
environments and CI included — the committed dumps are read-only inputs:
do not regenerate or hand-edit `schemas/provider/` there. `make schemas
CHECK=1` is the authoring-side pre-commit guard for pin bumps; extraction
output can legitimately vary with the local terraform and provider
versions, which is exactly why it happens in one place.
