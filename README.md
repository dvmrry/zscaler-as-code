# zscaler-as-code

Schema-driven Terraform boilerplate for managing Zscaler (ZIA + ZPA) where
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
- Python tooling is stdlib-only at a Python 3.8 syntax floor so it runs
  in restricted enterprise environments.
- All logic lives in `make` targets; CI pipelines are thin shells.

## Entry points

All workflows are `make` targets — run `make help` to list them. Do not
invent other invocation paths.

## Layout

    modules/<resource-type>/   GENERATED Terraform modules — never hand-edit
    schemas/provider/          pinned provider schema dumps (make schemas)
    schemas/tfvars/            generated JSON Schemas for config files
    tools/                     stdlib-only Python (3.8-floor) + overrides
    envs/{tenant-a,tenant-b}/  root modules, split state
    config/{tenant-a,tenant-b}/ fictional sample tfvars
    imports/{tenant-a,tenant-b}/ transform-emitted import blocks

Directories not yet present are created by later build phases.

## Regenerating provider schemas

`make schemas` runs `terraform providers schema -json` against the pinned
providers in `tools/schema-extract/` and rewrites `schemas/provider/`.
To bump a provider: edit the `version` pin in `tools/schema-extract/main.tf`,
run `terraform -chdir=tools/schema-extract init -upgrade`, then
`make schemas` and review the resulting git diff.
