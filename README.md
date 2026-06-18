# zscaler-as-code

Continuous Terraform reconciliation for Zscaler (ZIA + ZPA + ZCC):
module code is generated from the provider schemas, all configuration
lives in typed JSON (`.auto.tfvars.json`), existing tenants are adopted
via import blocks, and scheduled drift detection opens backfill PRs with
item-level diffs and audit-trail attribution.

**How this differs from [zscaler-terraformer](https://github.com/zscaler/zscaler-terraformer):**
the official tool is a one-shot exporter — it snapshots a tenant into
static hand-maintained HCL. This is a living pipeline: config stays
typed data, modules regenerate from schema dumps, and the tenant and
the repo are continuously reconciled through gated PRs.

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
| `make validate-config` | Validate config/ tfvars against generated JSON Schemas (dev-only; tries uv if jsonschema is not installed, skips gracefully if neither is available) |

`tools/registry.json` is the generated provider-surface catalog. Every
published ZIA/ZPA/ZCC Terraform resource has a generated module and tfvars
schema; only entries with a `fetch` block are live-read by `make fetch`.
`make headroom-report RESOURCE='zia zpa zcc'` shows the split between
fetch-managed resources, derived resources, generated-but-not-fetch-wired
resources, and known non-bulk/adoption holds.

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

See `pipelines/README.md` for Azure DevOps, GitHub Actions, and Bitbucket
examples — all thin shells over `make`. Point a pipeline definition at the
relevant example (or copy it) and adapt the agent pool and toolchain setup
to your environment.

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

## Deploying privately while consuming template updates

Run a private deployment repo as a downstream of this template (clone +
private remote; `git pull <template-remote> main` to update). Merges stay
trivial as long as the PATH CONTRACT holds:

- **Template-owned (never edit downstream):** `Makefile`, `tools/`,
  `modules/`, `schemas/`, `envs/demo/`, `config/demo/`, `pipelines/*.example.yml`,
  the docs. A local edit here is a future merge conflict and a fork —
  change the template via PR instead, or extend via the seams below.
- **Deployment-owned (never shipped by the template):** `config/<your-tenants>/`,
  `imports/<your-tenants>/`, `envs/<your-tenants>/`, `pulls/` (gitignored),
  `backend.conf`, your operative pipeline yamls (repo root or your own
  directory), and:
  - **`local.mk`** — custom make targets and variable overrides; the
    template Makefile `-include`s it and never ships it.
  - **`company/` (or any directory the template doesn't use)** — your
    scripts, docs, runbooks.
- **Derived-by-ritual:** operative pipeline yamls are adapted FROM the
  examples and do not update automatically — after pulling template
  updates, diff them against `pipelines/*.example.yml` (see
  pipelines/README.md).

## License

MIT — see [LICENSE](LICENSE).
