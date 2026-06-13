# Agent brief: resolve the `+ cbi_profile` plan delta (zero tenant writes)

This file is a coordination note on a temporary branch. Do NOT merge this
branch. Execute the steps below exactly, in order.

## Rules — read first

1. Do NOT improvise. Run each command exactly as written (after setting
   the three variables in Part 2). If an EXPECT does not match, STOP at
   that step and report: the step number, the command, and its output.
2. NEVER run `terraform apply`, `make apply`, or any command not listed
   here. Every command below is either read-only against the API or
   writes only to Terraform STATE (the backend blob). None of them
   changes the tenant.
3. When reporting output, replace tenant values (rule names, URLs,
   profile names) with `<redacted>`. Exit codes, step numbers, error
   text, and line numbers are fine to report verbatim.
4. Run everything from the REPO ROOT (the directory containing
   `Makefile` and `RUNBOOK.md`). Verify with:
   `test -f Makefile && test -f RUNBOOK.md && echo REPO_ROOT_OK`

## Why this delta exists (context, 30 seconds)

- The committed config has the rule's `cbi_profile` — it came from the
  API LIST endpoint, which returns it.
- Terraform state is populated by the provider's READ, which uses the
  DETAIL endpoint — and the detail endpoint OMITS `cbiProfile` for
  ISOLATE rules (vendor API bug, documented in the provider source).
- Plan = config minus state, so the plan offers `+ cbi_profile` — a
  write — on every run, forever. It never converges on its own.
- The fix: import THAT ONE RULE into state via the CLI (this CREATES
  the state file — no state needs to exist first), copy the config
  value into state with `make statefill`, then exclude that rule's
  import block. Every other resource keeps importing exactly as before.

## Part 1 — verify the checkout is current (do this FIRST)

The previous run failed because `make statefill` was missing. That
target exists in current main; if it is missing, the checkout is stale
or the Makefile is locally modified. Content checks (authoritative —
do not compare commit hashes):

Step 1.1
```
git status --porcelain Makefile
```
EXPECT: empty output. If ANYTHING prints, the Makefile has local edits
shadowing the pulled version — report the output and run
`git checkout -- Makefile`, then repeat this step.

Step 1.2
```
grep -c '^statefill:' Makefile
```
EXPECT: `1`. If `0`: the checkout predates the statefill feature. STOP
and report — the repo sync must be fixed before anything else works.

Step 1.3
```
grep -c 'STATE_AWARE' Makefile
```
EXPECT: `4`. (Reference: in current main these sit at lines 244, 245,
255, 345.) If not 4, same conclusion as 1.2: stale checkout. STOP.

Step 1.4 (sync-dating check only — NOT required by this task)
```
grep -c '^refresh-gates:' Makefile
test -f pipelines/commitback.sh && echo COMMITBACK_OK || echo COMMITBACK_MISSING
```
EXPECT: `1` and `COMMITBACK_OK`. Interpreting a mismatch:
- `refresh-gates` prints `0`: the sync predates the lint-gate fix.
  Re-sync the repo before continuing (the lint step needs it anyway).
- Only `COMMITBACK_MISSING`: the sync is exactly one commit behind
  (commitback.sh shipped 2026-06-12, after the statefill feature).
  This does NOT block this task — nothing below uses commitback.sh.
  CONTINUE to Part 2 now. Separately, re-sync when convenient: that
  commit also carries the fix for the commit-back step hanging after
  the branch push.

If 1.1–1.3 all matched EXPECT, the statefill sequence below runs on
this checkout regardless of Step 1.4. Only 1.2 or 1.3 failing is a
hard STOP.

## Part 2 — identify the rule and set variables

Step 2.1: Find the rule. In the most recent plan output, exactly one
`zia_url_filtering_rules` resource shows `+ cbi_profile`. Its address
looks like:
`module.zia_url_filtering_rules.zia_url_filtering_rules.this["SOME_KEY"]`
The part in quotes is the KEY.

Step 2.2: Find the rule's numeric id. Open
`imports/<your tenant label>/zia_url_filtering_rules_imports.tf` and
locate the import block whose `to =` line contains that same KEY. Its
`id = "..."` value is the RULE_ID. The block looks like:
```
import {
  to = module.zia_url_filtering_rules.zia_url_filtering_rules.this["SOME_KEY"]
  id = "1234567"
}
```

Step 2.3: Set three shell variables (fill in the real values; LABEL is
the tenant label used under `config/` and `envs/`):
```
LABEL=changeme
KEY=changeme
RULE_ID=changeme
```

Step 2.4: Confirm the config carries the field (the fill copies it):
```
grep -c 'cbi_profile' "config/$LABEL/zia_url_filtering_rules.auto.tfvars.json"
```
EXPECT: a number >= 1. If `0`, STOP and report — config must be
re-fetched + re-transformed before any fill.

## Part 3-PRE — WHERE to run Parts 3–5 (read before running anything)

Parts 3–5 need credentials for BOTH the state backend and the ZIA
provider. Machines outside the pipeline agents do not have backend
access — that is by design, not a problem to fix. If this machine
cannot reach the backend, do NOT run Parts 3, 4, or 5.1 here. Do this
instead:

REQUIRES the import-one target — `grep -c '^import-one:' Makefile`
must print `1`. If it prints `0`, re-sync the repo first (it shipped
2026-06-12); the raw-terraform fallback at the bottom of this section
works without it but is more error-prone.

1. Take the step below, replace the three REPLACE_ values with the
   literals found in Part 2 (paste literal values — no shell
   variables), and add it to the bootstrap pipeline in the SAME JOB as
   the plan step, BEFORE the stage-imports step. Give it the SAME
   `env:` block as the plan/fetch steps — it needs provider creds for
   the import GET, backend creds for the state write, AND `HTTPS_PROXY`
   if your egress is proxied (the import calls the ZIA API, same as
   fetch; a missing proxy makes it hang/timeout). Copy the env block
   verbatim from the fetch step, do not retype it.

```yaml
# TEMPORARY one-off — DELETE THIS STEP after one successful run.
# Reuse the fetch/plan step's full env: block (provider creds, backend
# creds, HTTPS_PROXY) — abbreviated here as a reminder, not a value.
- script: |
    set -e
    make import-one TENANT=REPLACE_LABEL RESOURCE=zia_url_filtering_rules \
      KEY=REPLACE_KEY IMPORT_ID=REPLACE_RULE_ID BACKEND_CONFIG=backend.conf \
      || echo "import returned non-zero (already managed from a prior run?) — statefill verifies next"
    make statefill TENANT=REPLACE_LABEL RESOURCE=zia_url_filtering_rules KEY=REPLACE_KEY \
      FIELD=cbi_profile BACKEND_CONFIG=backend.conf STATE_FILL=1
  displayName: One-off cbi_profile state fill (DELETE AFTER SUCCESS)
  env:
    # ↓ paste the SAME keys the fetch step uses (ZSCALER_*/ZIA_* creds,
    #   the backend storage key, and HTTPS_PROXY on proxied egress)
    ZSCALER_CLIENT_ID: $(ZSCALER_CLIENT_ID)
    # ... (all the rest, verbatim from fetch) ...
    HTTPS_PROXY: $(HTTPS_PROXY)   # omit only if egress is direct
```

   `make import-one` carries the `-var-file` the import REQUIRES (the
   env root's `items` variable has no default and the var-file is not
   auto-loaded from `config/`). Do NOT hand-write `terraform import` —
   without `-var-file` it dies on "No value for required variable".
   (If the plan step passes a BACKEND_CONFIG file other than
   `backend.conf` at the repo root, match that path here.)

2. In the SAME pipeline edit, find the existing stage-imports step and
   add `STATE_AWARE=1 BACKEND_CONFIG=backend.conf` to its make
   command. This change is PERMANENT, not temporary: it filters import
   blocks for already-managed addresses and does nothing otherwise.
   Without it, the plan fails on the now-managed rule with "Resource
   already managed".

3. Run the pipeline once. The single run then does: fill -> staged
   imports minus the managed rule -> plan -> assert-clean. EXPECT the
   plan summary `0 to change, 0 to destroy` with the other resources
   importing as before. Two failure texts in the temp step are SUCCESS
   signals on a re-run — state is already correct, delete the step:
   - `Resource already managed by Terraform` (the import landed earlier)
   - `refusing to overwrite a non-empty value` (the fill landed earlier)
   Any OTHER error: STOP and report the step's log (values redacted).

4. After one green run: DELETE the temporary step (keep the
   stage-imports change), and report per Part 6.

Parts 3–5 below describe the same commands for direct execution on a
machine WITH backend access — skip them entirely if you used the
pipeline step above.

## Part 3 — import the ONE rule (this CREATES the state file)

`make import-one` does not require an existing state file: it reads the
object from the API (a GET — tenant untouched) and WRITES a new state
object to the backend containing just this rule. "There is no tfstate
yet" is the expected before-state, not a blocker. It does init +
import in one shot, and crucially passes the `-var-file` the import
REQUIRES (the env root's `items` variable has no default and the
var-file is not auto-loaded from `config/`; a hand-written `terraform
import` without it dies on "No value for required variable").

PRE: provider creds must be in the environment (same vars as `make
fetch`), plus `HTTPS_PROXY` if egress is proxied — the import GET hits
the ZIA API exactly like fetch.

Step 3.1: Import the rule (state-only; zero tenant writes). Use the
SAME BACKEND_CONFIG the plan pipeline uses (assumed `backend.conf` at
repo root; adjust only if your pipeline passes a different one):
```
make import-one TENANT="$LABEL" RESOURCE=zia_url_filtering_rules KEY="$KEY" IMPORT_ID="$RULE_ID" BACKEND_CONFIG=backend.conf
```
EXPECT: ends with "Import successful!". If it errors "Resource already
managed", that is fine — continue. If it says `^import-one` is not a
target, the checkout predates 2026-06-12 — re-sync, or use the
raw-terraform fallback in Part 3-ALT below. Any other error: STOP and
report.

## Part 3-ALT — raw-terraform fallback (ONLY if import-one is absent)

Use this ONLY if `grep -c '^import-one:' Makefile` printed `0` and you
cannot re-sync right now. Two commands; the second MUST carry the
`-var-file` or it fails on the required `items` variable:
```
terraform -chdir="envs/$LABEL/zia_url_filtering_rules" init -input=false -reconfigure -backend-config="$(pwd)/backend.conf" -backend-config="key=$LABEL/zia_url_filtering_rules.tfstate"
terraform -chdir="envs/$LABEL/zia_url_filtering_rules" import -input=false -var-file="$(pwd)/config/$LABEL/zia_url_filtering_rules.auto.tfvars.json" "module.zia_url_filtering_rules.zia_url_filtering_rules.this[\"$KEY\"]" "$RULE_ID"
```
EXPECT: "Import successful!". Same proxy/creds prerequisites as Part 3.

## Part 4 — fill the field into state

Step 4.1: Preview (writes nothing):
```
make statefill TENANT="$LABEL" RESOURCE=zia_url_filtering_rules KEY="$KEY" FIELD=cbi_profile BACKEND_CONFIG=backend.conf
```
EXPECT: a summary including `filled zia_url_filtering_rules
items[...].cbi_profile from committed config`, a `serial N -> N+1`
line, and `PREVIEW ONLY` at the end. If it prints `error:` instead,
STOP and report the error line — every refusal names its exact cause.

Step 4.2: Push (the one state write):
```
make statefill TENANT="$LABEL" RESOURCE=zia_url_filtering_rules KEY="$KEY" FIELD=cbi_profile BACKEND_CONFIG=backend.conf STATE_FILL=1
```
EXPECT: ends with `state filled — next: make stage-imports ...`.

Step 4.3: Verify state now carries the value:
```
terraform -chdir="envs/$LABEL/zia_url_filtering_rules" state show "module.zia_url_filtering_rules.zia_url_filtering_rules.this[\"$KEY\"]" | grep -c cbi_profile
```
EXPECT: a number >= 1. If `0`, STOP and report — the fill did not land.

## Part 5 — exclude the now-managed rule and re-plan

NOTE: Step 5.1 also needs backend access (the filter checks state).
On a no-backend machine this is covered by item 2 of Part 3-PRE (the
permanent STATE_AWARE addition to the pipeline's stage-imports step).

Step 5.1: Re-stage imports, skipping anything already in state:
```
make stage-imports TENANT="$LABEL" STATE_AWARE=1 BACKEND_CONFIG=backend.conf
```
EXPECT: exit 0. The staged imports for zia_url_filtering_rules no
longer contain the `$KEY` block.

Step 5.2: Re-run the plan the normal way (the bootstrap pipeline, or
the make plan target the pipeline uses).
EXPECT: the `$KEY` rule shows NO changes at all (the provider preserves
the filled value), every other resource still shows as an import, and
the plan summary reads `0 to change, 0 to destroy`. assert-clean green.

## Part 6 — report back

Report, in this order:
1. Part 1 results: which of 1.1–1.4 matched EXPECT (yes/no each).
2. The step number you stopped at, if any, plus the exact error text
   (values replaced with `<redacted>`).
3. If you reached Part 5: the plan summary line and whether
   assert-clean passed.
