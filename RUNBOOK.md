# Runbook — Adoption and Drift Detection

Step-by-step procedures for bringing an existing Zscaler tenant under
management and keeping it there. Follow each step in order; commands are
exact and can be run verbatim.

---

## Prerequisites

**Credentials.** The fetcher and the Terraform providers share the same
environment variables. See `tools/FETCH.md` for the full contract and both
auth modes (OneAPI and legacy). Credentials are read from the environment
at runtime only. Never commit them or paste them into any tracked file.

**Corporate TLS inspection.** Enterprise networks that terminate HTTPS present
a corporate root CA that Python does not trust by default. Export the root
certificate and set:

```
export REQUESTS_CA_BUNDLE=/path/to/corp-root-ca.pem
```

Verify connectivity before fetching:

```
make fetch-diag
```

The diagnostic probes the fetcher's target hosts and prints the certificate
chain or the exact failure. Fix any error here before proceeding.

**Toolchain.** Terraform >= 1.5 and GNU make must be on `$PATH`. Verify with
`make env`. Python 3.6+ stdlib only — no pip install needed.

**Real pulls are gitignored.** `pulls/` must never be committed.

---

## Rehearsal (no credentials required)

Clone the repo on any machine with Terraform >= 1.5, GNU make, and Python 3.6+.
No Zscaler credentials or network access are needed for this path.

```
git clone https://github.com/dvmrry/zscaler-as-code
cd zscaler-as-code
make test            # full unit suite
make test-modules    # mock-provider module tests
make test-envs TENANT=demo   # smoke tests against the committed demo dataset
```

After `make test-envs TENANT=demo` succeeds, inspect `config/demo/` to see what
typed Terraform config looks like for a real tenant, and `imports/demo/` for the
matching import blocks. This is a faithful walkthrough of the full pipeline using
public-shaped data — no secrets required.

---

## Bootstrap — Adopting an Existing Tenant

Choose a short, opaque label (e.g. `prod`, `staging`). It becomes a directory
key used throughout.

### 1. Fetch live configuration

```
make fetch TENANT=<label>
```

Writes API JSON to `pulls/<label>/`. Requires credentials in the environment.

### 2. Transform to typed config

```
make transform IN=pulls/<label> TENANT=<label>
```

Produces:
- `config/<label>/<type>.auto.tfvars.json` — the tenant as typed config
- `imports/<label>/<type>_imports.tf` — ready-made import blocks

A drop report is printed listing fields the provider cannot manage. Review
it before continuing.

### 3. Type-check the generated config

```
make typecheck TENANT=<label>
```

Reports every field whose value does not fit the provider schema, with a
suggested fix on each line. Each suggestion is self-contained — follow it
without asking for clarification. Only lines whose suggestion says "relay the
field" require a human decision; all others are mechanical fixes (re-run
`make transform`, or add the field to `split_csv` in
`tools/overrides/<type>.json`).

Exit 0 means all config files type-check clean; exit 1 means mismatches were
found. Fix and re-run `make transform` until this step exits 0.

### 3a. Lint the config (semantic checks)

```
make lint TENANT=<label>
```

Catches type-correct config that is still operationally wrong: pasted
invisible characters, URL entries with schemes or CSV remnants, invalid
IP/CIDR values, duplicates in set-typed fields (terraform dedupes sets —
guaranteed perma-drift), colliding rule orders, values outside known
provider runtime-validator ranges, and cross-category URL shadowing —
an entry more specific than another category's entry silently pulls that
traffic out of the policies matching the broader entry (e.g. an SSL
bypass). ERRORs gate; WARNs (like shadowing) need a human glance —
shadowing is sometimes intended. Hand-edited files failing the
canonical-form check are fixed with `make fmt-config TENANT=<label>`.

### 4. Acknowledge drops (one-time, per field)

For each reported field, add it to `acknowledged_drops` in
`tools/overrides/<type>.json`:

```json
{
  "acknowledged_drops": ["field_name_here"]
}
```

Create the file if it does not exist. Once acknowledged, the report stays
quiet for that field; only new drops surface on future runs. Re-run
`make transform` to confirm the report is clean.

### 5. Review the generated config

```
git diff config/<label>/
```

This is the tenant as typed config. If anything looks wrong, fix the
transform or an override map. Never hand-edit `config/<label>/` directly —
it is overwritten on the next `make transform`.

### 6. Generate env roots

```
make gen-env TENANT=<label>
```

Creates `envs/<label>/<type>/` root modules. Safe to re-run.

### 7. Plan each resource type (expect N imports, 0 changes)

For each resource type in `imports/<label>/`:

```
cp imports/<label>/<type>_imports.tf envs/<label>/<type>/
make plan TENANT=<label> RESOURCE=<type>
```

Expected result: **N imports, 0 changes.** Terraform will import the
existing objects and apply no modifications.

If the plan shows changes, the transform and the live tenant disagree. Do
not apply until you understand the difference. Common fixes: add a
`drop_if_default` override or a `key_field` override in
`tools/overrides/<type>.json`, then re-run `make transform` and re-check.
Never hand-edit `config/<label>/` to force the plan clean.

### 7a. Remote state (Azure Blob Storage) — before first apply

Plans are stateless, but the first apply writes tfstate. To use Azure
Blob Storage (one blob per resource root, key `<tenant>/<type>.tfstate`):

1. Copy `backend.conf.example` to `backend.conf` and fill the three
   values (storage account must already exist; the pipeline/CLI identity
   needs **Storage Blob Data Contributor** on the container).
2. Add the backend block to the tenant's roots (recorded in
   `envs/<label>/.backend`, so later regenerations keep it):

```
make gen-env TENANT=<label> BACKEND=azurerm
```

3. From here on, pass the config to every plan/apply init:

```
make plan TENANT=<label> BACKEND_CONFIG=backend.conf
```

Authentication is environment-only (pipeline service connection,
`az login`, or `ARM_*` vars) — never written into `backend.conf`.
Mock-provider tests are unaffected (they init with `-backend=false`).
If a root was already applied with LOCAL state, migrate once with
`terraform -chdir=envs/<label>/<type> init -migrate-state
-backend-config=<file> -backend-config="key=<label>/<type>.tfstate"`.

### 8. Apply and remove import blocks

Apply using your Terraform invocation or CI pipeline. After state is
populated, remove the import blocks file:

```
rm envs/<label>/<type>/<type>_imports.tf
```

Import blocks error once resources are already managed. Removal is required.

### 9. Commit config

```
git add config/<label>/ imports/<label>/
git commit -m "Adopt <label> tenant"
```

Push to your private repo. Do not push to this template repo.

---

## Drift Detection — Steady State

```
make drift TENANT=<label>
```

Runs fetch + transform and compares the result against committed config.

- **Exit 0**: no drift.
- **Exit 3**: drift detected — the git diff is the report.

Reading the diff:

- **Value changes**: click-ops edits made outside Terraform. Revert in the
  console or update config and apply.
- **New keys / new objects**: unmanaged resources. Fresh import blocks are
  already in `imports/<label>/`; cherry-pick the relevant type file and
  follow steps 6–7 to adopt them.

After addressing drift, run `make plan TENANT=<label>` for the state-side
check.

### Automated backfill PRs

For tenants where admins make console/API changes without warning, the
drift pipeline can open the backfill PR itself (full reference flows in
`pipelines/azure-pipelines-drift.example.yml` and the GitHub example):

1. **Scheduled, two cadences** — hourly scoped to the hot-path resource
   (`make drift TENANT=<label> RESOURCE=<type>` fetches just that one),
   weekly for the broad sweep.
2. **Drift (exit 3) → branch → PR.** The PR body is
   `tools/drift_summary` (item-level: added / removed / changed-with-
   fields) plus `tools/audit` — the ZIA audit trail for the window,
   answering WHO made the change. Attribution is strictly advisory: any
   audit failure degrades to a one-line note, never blocks the PR.
3. **Merge-readiness check — a human merges.** The PR's plan job runs
   `make plan-changed SAVE=1 ... && make assert-clean`. Backfill means
   config now MATCHES the tenant, so every saved plan must be pure
   no-op (imports of new objects allowed). Green = faithful snapshot;
   the reviewer reads the body (what changed, who changed it) and
   clicks merge — staying aware of every change without doing any of
   the legwork. Red = the tenant moved again or transform disagrees —
   look closer first. Merging never touches the tenant; new objects
   enter state via the delivery pipeline's import apply.

---

## Adding a Resource Type

1. Add one entry to `tools/registry.json` (plus `tools/overrides/<type>.json`
   if the resource has quirky fields).
2. Regenerate and test:

```
make generate
make gen-env TENANT=<label>
make test
```

3. Commit. All module code, JSON Schemas, and env roots are generated.

New registry entries are automatically conformance-tested (`make test` /
`make conformance`) — schema-driven adversarial synthesis through the full
transform and a nesting-aware structural typecheck against the complete quirk
catalog — before any tenant contact.

---

## Provider Bumps

1. Edit the version pin in `tools/schema-extract/main.tf`.
2. `terraform -chdir=tools/schema-extract init -upgrade`
3. `make schemas && make generate`
4. Review the git diff — drop-report changes mean coverage changed; update
   `acknowledged_drops` entries as needed.
5. `make test`
6. Commit the lock file, `schemas/provider/`, generated modules, and any
   override changes together.

---

## Validation Order

Always validate against a development tenant before production. Both auth
modes are supported; the fetcher resolves mode from
`ZSCALER_USE_LEGACY_CLIENT`. See `tools/FETCH.md`.

---

## Troubleshooting

| Symptom | Action |
|---|---|
| TLS / certificate errors | `make fetch-diag`; set `REQUESTS_CA_BUNDLE` |
| `"key field missing"` transform error | Set `key_field` in `tools/overrides/<type>.json` |
| Duplicate derived keys | Set `key_field` to a field unique across objects |
| Plan shows phantom diffs after adoption | Add field to `drop_if_default` in `tools/overrides/<type>.json`; re-transform |
| CHECK gate failure in CI | Run `make generate` and commit; never hand-edit `modules/` or `schemas/tfvars/` |
| `import blocks error: resource already managed` | Delete `_imports.tf` from the env root after first apply |
| Plan rejects a predefined/system object (e.g. order -1) | Add a `skip_if` matcher to `tools/overrides/<type>.json` (e.g. `"skip_if": [{"default_rule": true}]`); run `make transform` — the item is excluded from config and imports with a stderr note |
| `Too many <field> blocks` at plan/test | Stale config from before the max_items merge — `git pull && make transform`; max_items=1 blocks are ONE object with list members (e.g. `departments: {"id": [..]}`) |
| Plan rejects a value the schema allows (e.g. `size_quota`) | Provider runtime validator (not in the schema dump). If the API uses 0/empty for "not set", add the field to `drop_if_default`; otherwise relay the one-line error |
