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

Step 1.4
```
test -f pipelines/commitback.sh && grep -c '^refresh-gates:' Makefile
```
EXPECT: `1`. This proves the checkout is at or after the two newest
changes. If the file is missing or grep prints 0, the sync stopped at
an older commit. STOP and report which of 1.2–1.4 failed.

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

## Part 3 — import the ONE rule (this CREATES the state file)

`terraform import` does not require an existing state file: it reads
the object from the API (a GET — tenant untouched) and WRITES a new
state object to the backend containing just this rule. "There is no
tfstate yet" is the expected before-state, not a blocker.

Step 3.1: Init the env root against the backend. Use the SAME backend
config file the plan pipeline uses (assumed `backend.conf` at repo
root; adjust the filename only if your pipeline passes a different
BACKEND_CONFIG):
```
terraform -chdir="envs/$LABEL/zia_url_filtering_rules" init -input=false -reconfigure -backend-config="$(pwd)/backend.conf" -backend-config="key=$LABEL/zia_url_filtering_rules.tfstate"
```
EXPECT: "Terraform has been successfully initialized!".

Step 3.2: Import the rule (state-only; zero tenant writes):
```
terraform -chdir="envs/$LABEL/zia_url_filtering_rules" import -input=false "module.zia_url_filtering_rules.zia_url_filtering_rules.this[\"$KEY\"]" "$RULE_ID"
```
EXPECT: "Import successful!". If it errors "Resource already managed",
that is fine — continue. Any other error: STOP and report.

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
