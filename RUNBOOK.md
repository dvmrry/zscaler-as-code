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
make lock TENANT=<label>
```

Creates `envs/<label>/<type>/` root modules and pins provider artifact
HASHES per root (lock files; commit them with the roots). Safe to re-run.

### 7. Plan each resource type (expect N imports, 0 changes)

For each resource type in `imports/<label>/`:

```
cp imports/<label>/<type>_imports.tf envs/<label>/<type>/
make plan TENANT=<label> RESOURCE=<type> SAVE=1
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
make plan TENANT=<label> RESOURCE=<type> SAVE=1 BACKEND_CONFIG=backend.conf
```

Authentication is environment-only — never written into `backend.conf`:
a SAS token via the `ARM_SAS_TOKEN` secret variable (omit
`resource_group_name` in SAS mode — it triggers a management-plane
lookup SAS cannot perform), or Entra ID via a pipeline service
connection / `az login` / `ARM_*` vars with `use_azuread_auth = true`.
Both modes are laid out in `backend.conf.example`.
Mock-provider tests are unaffected (they init with `-backend=false`).
If a root was already applied with LOCAL state, migrate once with
`terraform -chdir=envs/<label>/<type> init -migrate-state
-backend-config=<file> -backend-config="key=<label>/<type>.tfstate"`.

### 8. Apply and remove import blocks

**If workstations cannot reach the state storage** (blob access locked
to pipeline agents), run the bootstrap pipeline instead of applying
locally: `pipelines/azure-pipelines-bootstrap.example.yml` stages the
imports, plans, PROVES the plan is imports-only (`assert-clean` before
the approval gate), and applies after approval — per tenant, optionally
per resource/product wave. Steps 7–8 collapse into one supervised run;
the staged import copies live only in the agent workspace, so there is
nothing to remove afterwards.

**Re-running bootstrap is safe and delta-only**: staging is state-aware
(`make stage-imports STATE_AWARE=1`) — import blocks whose address is
already managed are filtered out against `terraform state list`
(terraform errors on re-importing, so unfiltered re-runs would go red),
already-applied `moved` blocks are inherent no-ops, and an empty delta
plans to no-op. The same mechanism adopts NEW objects found by drift:
merge the drift PR, re-run bootstrap, only the new imports execute.
Applies are additionally refused off the default branch
(`make apply` checks the CI ref / local branch; `MAIN_BRANCH=` to
rename, `ALLOW_NON_MAIN=1` for deliberate exceptions), and each run
clears stale saved plans first (`make clean-plans`) so a cancelled
previous run can never leak its tfplans into the next apply.

Otherwise, apply the saved plan with:

```
make apply TENANT=<label> RESOURCE=<type> [BACKEND_CONFIG=backend.conf]
```

`make apply` applies only saved tfplan artifacts and refuses destroys or
replacements without `ALLOW_DESTROY=1`. If the import plan shows unexpected
destroys, do not proceed until you understand the cause — pass
`ALLOW_DESTROY=1` only after confirming the change is intentional.

After apply succeeds, remove the import blocks file:

```
make unstage-imports TENANT=<label>
```

Import blocks error once resources are already managed. Removal is required.

### 9. Commit config

```
git add config/<label>/ imports/<label>/ envs/<label>/
git commit -m "Adopt <label> tenant"
```

Push to your private repo. Do not push to this template repo.

---

## Drift Detection — Steady State

```
make drift TENANT=<label>
```

Runs fetch + transform and compares the result against committed config.

- **Exit 0**: no drift. **Non-zero**: the drift TOOL exits 3, but make flattens every failing recipe to exit 2 — so callers cannot read the 3. Distinguish drift from a real failure by the worktree: `git status --porcelain config/<label> imports/<label>` non-empty = drift (the git diff is the report); empty = the run itself failed (fetch/transform error — read the log).

Reading the diff:

- **Value changes**: click-ops edits made outside Terraform. Revert in the
  console or update config and apply.
- **New keys / new objects**: unmanaged resources. Fresh import blocks are
  already in `imports/<label>/`; cherry-pick the relevant type file and
  follow steps 6–7 to adopt them.
- **Renames**: when a console rename re-keys an item (same object id,
  different derived key), transform stages `imports/<label>/<type>_moves.tf`
  with `moved` blocks and prints RENAME(S) DETECTED. Copy that file into
  the env root alongside the imports file before plan/apply — the rename
  becomes a pure state-address change instead of a destroy+create of the
  live object. Delete the copy after apply, like import blocks.

After addressing drift, run `make plan TENANT=<label>` for the state-side
check.

### Automated backfill PRs

For tenants where admins make console/API changes without warning, the
drift pipeline can open the backfill PR itself (full reference flows in
`pipelines/azure-pipelines-drift.example.yml` and the GitHub example):

> The drift report carries live admin identities (email addresses) and
> tenant-derived values, so the drift pipeline must run in the operator's
> PRIVATE deployment repo — never the public template/fork, where the PR
> body would leak admin PII.

1. **Scheduled, two cadences** — hourly scoped to the hot-path resource
   (`make drift TENANT=<label> RESOURCE=<type>` fetches just that one),
   weekly for the broad sweep.
2. **Drift (non-zero run + changed worktree) → branch → PR.** The PR body is
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

## Editing Config by Hand

Post-adoption changes are JSON edits to `config/<label>/`. Wire editor
autocomplete + inline validation from the same schemas CI uses: merge
`schemas/tfvars/vscode.settings.example.json` (generated; one mapping per
resource type) into your `.vscode/settings.json`. After editing, the
usual gates apply: `make fmt-config TENANT=<label>` (canonical form),
`make lint TENANT=<label>`, `make typecheck TENANT=<label>` — then PR,
and the plan lands as a PR comment.

---

## Rolling Back a Merged Change

Config is git, so rollback is the same flow in reverse:

1. `git revert <merge-commit>` on a branch; open the PR. The plan
   comment shows the inverse change — review it like any other.
2. If the revert removes objects (you are reverting an addition), the
   apply will contain destroys and `make apply` refuses them without
   `ALLOW_DESTROY=1` — that is the design: destroys are a per-run human
   decision, including during rollbacks.
3. Merge → apply. Never roll back by clicking in the console: drift
   would just open a PR re-importing the console state, and you would
   be fighting your own pipeline.

## Break-Glass: Console First, Reconcile After

When a change cannot wait for a PR (incident response), make it in the
console. Reconciliation is automatic: the next drift run (hourly for
the hot-path resource) opens a backfill PR carrying the change AND the
audit-trail attribution of who made it. Merge it like any drift PR —
the live tenant already has the change, so the plan is no-op/imports
and `assert-clean` shows green. Never hand-edit `config/` to
"pre-record" a console change; let drift codify reality.

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
5. `make mine` — scans the NEW pinned provider source for behavioral
   quirks (runtime validators, unit conversions, inverted booleans,
   merge flattens, …) against override coverage. Exits 4 when the bump
   introduced a quirk no override encodes; each MISSING line names the
   override key to add. `tools/MINING.md` is the per-class verification
   procedure — follow it before encoding anything. Run
   `make issue-watch` too — upstream issue reports often explain a new
   quirk before any source reading does.
6. `make test`
7. `make lock TENANT=<label>` for each committed tenant — version pins
   alone don't pin artifact hashes; the per-root lock files do.
8. Commit the lock files, `schemas/provider/`, generated modules, and any
   override changes together.

---

## Validation Order

Always validate against a development tenant before production. Both auth
modes are supported; the fetcher resolves mode from
`ZSCALER_USE_LEGACY_CLIENT`. See `tools/FETCH.md`.

---

## Relaying Diagnostics Out (shape reports)

When plans or configs can't leave a restricted environment but a
diagnosis needs outside eyes, relay a **shape report** instead of the
artifact:

```sh
# from the repo root; the plan lives in the env root
terraform -chdir=envs/<label>/<type> show -json tfplan > plan.json
make shape FILE=plan.json [ONLY=<resource_type>]
```

Every value is replaced by a deterministic per-run token (`s1`, `id3`,
`n2`, item keys `k1`…); field names stay readable (they come from the
provider schema, not tenant data). Equal values share a token, so the
report still distinguishes the cases that matter — `REORDER only`
(same values, new positions) vs a value rewrite at a named path — and
a self-check refuses to emit if any input value would survive into the
output. Works on plan JSON, `config/*.auto.tfvars.json`, and raw pull
files. The output is small and safe to paste.

---

## Troubleshooting

| Symptom | Action |
|---|---|
| TLS / certificate errors | `make fetch-diag`; set `REQUESTS_CA_BUNDLE` |
| `"key field missing"` transform error | Set `key_field` in `tools/overrides/<type>.json` |
| Duplicate derived keys | Set `key_field` to a field unique across objects |
| Plan shows phantom diffs after adoption | Add field to `drop_if_default` in `tools/overrides/<type>.json`; re-transform |
| CHECK gate failure in CI | Run `make generate` and commit; never hand-edit `modules/` or `schemas/tfvars/` |
| `import blocks error: resource already managed` | Delete `_imports.tf` from the env root after first apply (or stage with `STATE_AWARE=1`, which filters these out) |
| Plan re-orders many/all rules of a type | ZIA `order` has insert-shift semantics: a rule added/removed (console, script, or a partial apply) cascades order changes to every neighbor. Run `make drift TENANT=<label> RESOURCE=<type>`; the backfill PR (with a MASS CHANGE banner) adopts the tenant's CURRENT order. Never apply stale orders over it blindly — that re-shuffles live enforcement |
| De-scoping an item AFTER it was imported (e.g. adding a `skip_if` later) | The item is in state but gone from config — the plan proposes a DESTROY. Never ALLOW_DESTROY for this: `make forget TENANT=<label> RESOURCE=<type> KEY=<config key> [BACKEND_CONFIG=backend.conf]` removes it from state only (run on an agent when workstations lack blob access); the object stays in the tenant, unmanaged |
| `Error acquiring the state lock` after a KILLED run | The azurerm blob lease never self-expires. Verify no run is active, then `make unlock TENANT=<label> RESOURCE=<type> LOCK_ID=<uuid from the error> BACKEND_CONFIG=backend.conf` — on an agent when workstations lack blob access (`pipelines/azure-pipelines-unlock.example.yml`), or have a storage admin break the blob lease directly |
| DNS error on `make fetch` token request | Verify `ZSCALER_VANITY_DOMAIN` is your vanity subdomain (not the cloud name); the token endpoint is constructed as `<vanity>.zslogin[<cloud>].net` — a typo causes an immediate DNS resolution failure that the fetch error output attributes to proxy/egress (it is not) |
| `missing required env var <ZIA_API_KEY\|ZIA_USERNAME\|ZIA_PASSWORD\|ZIA_CLOUD\|ZPA_CLIENT_ID\|ZPA_CLIENT_SECRET\|ZCC_CLIENT_ID\|ZCC_CLIENT_SECRET\|ZCC_CLOUD>` after setting `ZSCALER_USE_LEGACY_CLIENT` | Legacy mode needs a separate full credential set — see `tools/FETCH.md` **Legacy** section. Switching from OneAPI requires re-exporting all nine vars; only the first missing one is named in the error |
| Plan rejects a predefined/system object (e.g. order -1) | Add a `skip_if` matcher to `tools/overrides/<type>.json` (e.g. `"skip_if": [{"default_rule": true}]`); run `make transform` — the item is excluded from config and imports with a stderr note |
| `Too many <field> blocks` at plan/test | Stale config from before the max_items merge — `git pull && make transform`; max_items=1 blocks are ONE object with list members (e.g. `departments: {"id": [..]}`) |
| Plan shows destroy+create of the SAME object after a console rename | Copy `imports/<label>/<type>_moves.tf` (staged by `make transform`/`make drift`) into the env root; the moved blocks turn it into a state-address change |
| Plan rejects a value the schema allows (e.g. `size_quota`) | Provider runtime validator (not in the schema dump). If the API uses 0/empty for "not set", add the field to `drop_if_default`; otherwise relay the one-line error |
| `make transform` reports unacknowledged dropped fields (new API surface) | `make triage IN=pulls/<label> APPLY=1` — classifies every path, auto-acknowledges the provably-safe classes (id-only-block decoration, audit metadata, SDK-modeled-but-provider-ignored), and exits 4 listing only SYNONYM/UNKNOWN paths that need eyes. A SYNONYM (leaf sharing tokens with a same-level schema field) is the signingCertId class: verify the provider read/expand per tools/MINING.md, then encode `renames` or acknowledge — never acknowledge it unverified |
| A console-visible setting is missing from config (e.g. location latitude/longitude, `capture_pcap`, app segment `adp_enabled`) | The pinned provider has NO schema field for it — it is console-managed surface, acknowledged in the override's `acknowledged_drops` (the provider cannot read or write it, so terraform cannot clobber it by commission). One caveat to verify ONCE per resource type: after the first provider UPDATE of such a resource, spot-check in the console that one console-managed setting survived the PUT — if the API resets omitted fields, relay it and we escalate upstream |
| ZPA app connector group write 400s naming `signingCertId` | The tenant was migrated to OAuth2 connector enrollment (a ZPA backend rollout, not a provider change — provider issue #650). The provider's name for it is `enrollment_cert_id`; it auto-resolves the "Connector" cert on CREATE only — updates send exactly what config carries. Re-run `make transform` (the override renames the API's `signingCertId` into config); if the pull carries no cert id at all, set `enrollment_cert_id` on the items by hand from the console's enrollment cert |
