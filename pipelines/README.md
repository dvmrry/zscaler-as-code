# Pipeline examples

Reference material, not working configs. Copy the file for your CI
platform into the location it expects, fill the `FILL-` markers, and
adapt agent/runner specifics (pools, terraform availability, proxy).
All logic lives in the Makefile — pipelines stay thin shells over
`make` so the same behavior runs identically on a laptop and any CI
platform (AGENTS.md rule 9).

The landscape is three pipelines:

| Pipeline | Trigger | Credentials | State | Make targets |
|---|---|---|---|---|
| **Validation** (PR gate) | every PR | none | never touched (`-backend=false` everywhere) | `test`, `validate`, `typecheck`, `lint`, `test-envs`, `validate-imports` |
| **Plan → Apply** (delivery) | merge / manual | real API creds + state auth | locked during plan/apply | `plan-changed SAVE=1` → approval → `apply` |
| **Bump check** (scheduled) | weekly cron | none (public registries) | not used | `bump-check` → orange run (`SucceededWithIssues`) + deduplicated ADO work item on a board (no webhooks/email needed); red = the check itself failed |
| **Drift** (scheduled) | cron (hourly scoped + weekly broad) | read-only API creds | not used | `drift [RESOURCE=…]` → non-zero + changed worktree (make flattens the tool's exit 3) → backfill PR (`drift-report` output: drift summary + audit body); `assert-clean` shows merge-readiness, a human merges |

Notes that apply to every platform:

- **GitHub Actions token permissions**: the drift job pushes a backfill
  branch and opens a PR using `github.token`. GitHub repositories default
  to read-only token permissions for Actions. The drift job must declare
  `permissions: { contents: write, pull-requests: write }` — without it
  the push and `gh pr create` return 403 and the backfill PR is never
  created. (ADO equivalent: build service identity granted **Contribute**
  on the repository.)
- **Full git history for `plan-changed`**: the diff-derived plan targets
  need the merge-base with the target branch. Shallow clones break it —
  set fetch depth 0 (examples below do).
- **Apply scope is the saved-plan artifacts.** Stage 1 publishes
  `envs/**/tfplan` (and the plan text for the approval screen); stage 2
  runs `make apply`, which only ever applies those artifacts. There is
  no apply-all to misfire. `make apply` additionally refuses a plan
  that destroys anything unless `ALLOW_DESTROY=1` is passed — destroys
  require a human to opt in per run, on purpose.
- **Plan artifacts can contain sensitive values.** Restrict artifact
  visibility/retention; never publish them outside the pipeline.
- **Serialize applies** per tenant (ADO environment exclusive lock /
  GitHub environment concurrency) so two merges can't interleave.
- **Manual scoped runs**: every platform's manual-run parameters map
  directly to make variables — `make plan TENANT=<t> RESOURCE=<rt>`.
- **Multi-tenant credentials without YAML case statements**: hold each
  tenant's secrets once under tenant-scoped names — tenant-first
  (`ZS2_ZSCALER_CLIENT_ID`) or product-first (`ZSCALER_ZS2_CLIENT_ID`);
  tenant-first wins, and a both-set-with-different-values conflict is
  warned, never silent — then resolve in a step with
  `eval "$(python3 -m tools.cred_env <tenant>)"`. The mapping is a
  tested allowlist in code; tenant-specific values stay pipeline-side.
  Don't run that step under `set -x`.
- **Agents without terraform**: `make install-tf VERSION=1.15.4`
  downloads and checksum-verifies the binary into `bin/`; either PATH
  it or pass `TF=bin/terraform` to subsequent make calls.
- **Plan as a PR comment**: `make plan-report` renders every saved
  tfplan to `reports/plan.md` (per-root fenced blocks); posting is one
  platform line — `gh pr comment --body-file` (GHA), the PR-threads
  REST call with `System.AccessToken` (ADO), the 2.0 comments API
  (Bitbucket) — each with a truncation guard for the platform's comment
  size cap (full text stays in the artifact). Plan text can contain
  sensitive values: private repos only, same caveat as tfplan artifacts.
- **Delta reports as artifacts**: `make drift-report TENANT=<t>` renders
  the drift summary + audit attribution to `reports/<t>/drift.md` — one
  file, three audiences. Publishing is native everywhere (ADO `publish:`
  pipeline artifact, GHA `upload-artifact`, Bitbucket `artifacts:`).
  Inline run-summary rendering is the only platform-uneven part: ADO
  `##vso[task.uploadsummary]`, GHA `$GITHUB_STEP_SUMMARY`, Bitbucket
  none (artifact download only). The PR description carries the same
  markdown regardless, so reviewers never depend on the platform extra.
