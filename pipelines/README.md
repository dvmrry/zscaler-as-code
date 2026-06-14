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
| **Validation** (PR gate) | every PR | none | never touched (`-backend=false` everywhere) | `test`, `lint-pipelines`, `validate`, `typecheck`, `lint`, `test-envs`, `validate-imports` (the ADO example ships `test-envs`/`validate-imports` commented — tenant-specific wiring is an adopter choice; the GitHub/Bitbucket examples run them too) |
| **Plan → Apply** (delivery) | merge / manual | real API creds + state auth | locked during plan/apply | `plan-changed SAVE=1` → approval → `apply` |
| **Bootstrap** (manual, per tenant/wave) | run-pipeline button | real API creds + state auth | locked; writes the FIRST state (imports only — never mutates the tenant) | optional agent-side refresh (`fetch` → `DROPS_CHECK=1 transform` → inline gates → **commit-back PR**, drift-style) → `stage-imports` → `plan SAVE=1` → `assert-clean` (imports-only proof, BEFORE approval) → approval → `apply` |
| **Bump check** (scheduled) | weekly cron | none (public registries) | not used | `bump-check` → orange run (`SucceededWithIssues`) + deduplicated ADO work item on a board (no webhooks/email needed); red = the check itself failed. Second step: `issue-watch` → orange on NEW upstream issues/PRs mentioning our resource types (other operators hit problems first — the signingCertId class); triage, then `UPDATE_BASELINE=1 make issue-watch` and commit |
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
- **Applies run from the default branch only**: `make apply` refuses
  other refs (reads ADO/GHA/Bitbucket ref vars, falls back to the local
  git branch; `MAIN_BRANCH=` overrides the name, `ALLOW_NON_MAIN=1` is
  the deliberate escape hatch). Only merged config gets applied.
- **Examples are templates, not live pipelines — re-sync after pulling.**
  Fixes landing in these example files do NOT propagate to the operative
  pipeline definitions adapted from them (field-hit twice: a scoped run
  applied stale out-of-scope plans because the live yaml predated the
  clean-plans and scoped-apply fixes). After updating the repo, diff your
  operative yamls against the current examples. On ADO self-hosted
  agents, also set `workspace: clean: all` on plan/apply jobs — a fresh
  workspace per run kills the stale-artifact class at the platform layer.
  Corollary: cleans wipe GENERATED files at every job start while
  checked-out files come back free — a runtime-generated `backend.conf`
  must be materialized in EVERY job that uses it (or committed to the
  private deployment repo, making the question moot).
- **Stale-plan hygiene on reused agents**: self-hosted workspaces
  persist between runs, so a cancelled run's tfplans would ride into
  the next apply (apply's scope IS the artifacts). `plan-changed`
  clears stale plans automatically; pipelines calling bare `make plan
  SAVE=1` (bootstrap) run `make clean-plans` first. **And the apply
  stage passes the SAME tenant/scope to `make apply`** — defense in
  depth: even a contaminated artifact cannot apply outside the run's
  declared scope (field-hit: a ZIA-targeted run applied stale ZPA
  plans from a previous cancelled run).
- **Apply scope is the saved-plan artifacts.** Stage 1 publishes
  `envs/**/tfplan` (and the plan text for the approval screen); stage 2
  runs `make apply`, which only ever applies those artifacts. There is
  no apply-all to misfire. `make apply` additionally refuses a plan
  that destroys anything unless `ALLOW_DESTROY=1` is passed — destroys
  require a human to opt in per run, on purpose.
- **Plan artifacts can contain sensitive values.** Restrict artifact
  visibility/retention; never publish them outside the pipeline.
- **The approval gate is PORTAL configuration, not yaml.** Naming an
  ADO environment in a deployment job does nothing by itself — add an
  Approvals check (and Exclusive Lock) on the environment in the
  Environments UI, or the apply stage runs unreviewed. The reviewer's
  first read is the counts-first summary table at the top of
  `reports/plan.md` (run Summary tab / artifact): per-root
  import/add/change/destroy counts with a loud banner when any destroys
  are present.
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
  The auth mode itself (`ZSCALER_USE_LEGACY_CLIENT`) takes the same
  prefix treatment — set it per-tenant (`ZS2_ZSCALER_USE_LEGACY_CLIENT`)
  OR set one bare, unprefixed `ZSCALER_USE_LEGACY_CLIENT` to cover every
  tenant (whole-legacy or whole-OneAPI deployments); a tenant-prefixed
  flag overrides the bare one, so a single tenant can opt to the other
  mode. cred_env re-emits the resolved flag canonically, so the bare
  global value is honored, not clobbered.
  It exports only the active auth mode's variables (legacy vs OneAPI,
  never a mix) and fails loud if a product's set is half-configured, so a
  stray wrong-mode var can't crash a provider; the resolved mode and safe
  vars are echoed to stderr (secrets shown as `set`; tenant-identifying
  values like the vanity domain and customer id hidden unless
  `FETCH_DEBUG=1`). Don't run that step under `set -x`.
- **Deduplicate the auth env block** (ADO): every authenticating step
  (`fetch`/`plan`/`drift`/`import`/`apply`) needs the same `eval cred_env`
  + secret mapping; copy-pasting it per step is how a variable gets dropped
  or mistyped. `pipelines/steps/zscaler-auth.yml` is a reusable step
  template that holds it once — reference it with `tenant` + `command`
  (+ `secrets` and `stateToken`) instead of repeating the block. The key
  fact it leans on: **only SECRET variables need explicit `env:` mapping** —
  non-secret variables from a linked variable group are already exposed to
  every step's environment. So `*_CLOUD`, `*_ZPA_CUSTOMER_ID`, `HTTPS_PROXY`,
  and **`*_ZSCALER_USE_LEGACY_CLIENT`** need no mapping; keep the legacy flag
  as an ordinary (non-secret) variable in the group, never a hand-typed env
  line. `tenant` must be compile-time (a `parameter` or `${{ variables.X }}`),
  since ADO doesn't substitute macros in env-var keys. (See the bootstrap and
  drift examples.)
- **Deduplicate the per-job preamble** (ADO, self-hosted agents):
  `pipelines/steps/job-setup.yml` is the companion to the auth template for
  the *structural* repetition — checkout, the pinned `make install-tf`, and
  optional `backend.conf` materialization — that recurs once per job on agents
  which lack terraform or clean the workspace each run. Reference it at the top
  of a job, then add the per-command `zscaler-auth.yml` step(s):
  ```yaml
  steps:
    - template: steps/job-setup.yml
      parameters: { installTf: 1.15.4, backendConf: true }
    - template: steps/zscaler-auth.yml
      parameters: { tenant: ${{ parameters.tenant }}, command: make plan ... }
  ```
  Its three knobs — `installTf` (a version to install+PATH), `backendConf`
  (materialize from `STATE_*` vars), and `persistCredentials: true` (for
  commit-back jobs that push a branch) — all default off, so on hosted agents
  (terraform present, `backend.conf` committed) it's just a consistent
  `checkout`. It deliberately does **not**
  bundle auth (credentials are per-command — a job authenticates more than
  once) and does **not** set `workspace: clean: all` (a job property, not a
  step). Its main payoff is the terraform-version invariant: pin the version in
  ONE template reference instead of in every job, so it can't drift.
- **Catch pipeline drift before it ships** (`make lint-pipelines`): the same
  inconsistencies that keep biting — a terraform version bumped in one pipeline
  but not its siblings, a hand-rolled auth `env:` block that drops a var
  (the `Plugin did not respond` provider crash), a non-secret config var typed
  into a step `env:` (where it gets dropped) instead of the variable group, a
  runtime `$(...)` tenant the template can't use, a split `backend.conf`
  strategy (this last one a warning; the rest fail the gate) — are checked by
  `make lint-pipelines`. It's a stdlib gate (no YAML
  library; it scans the YAML as structured text), so it lives in the repo and
  updates on pull — run it deployment-side over your operative pipelines:
  `make lint-pipelines DIR=<your pipelines dir>` (optionally
  `TF_VERSION=<pin>` to assert a specific version, `STRICT=1` to gate on
  warnings). If your pipelines share a directory with other YAML — the repo
  root, alongside the shipped `pipelines/*.example.yml` — name the operative
  files instead of scanning a tree, so the cross-file rules don't compare your
  pipelines against the examples: `make lint-pipelines FILES="azure-pipelines-bootstrap.yml azure-pipelines-drift.yml"`.
  Each rule is grounded in an incident this project actually hit;
  it's the automated form of the "re-sync after pulling" advice above. Add it
  as a PR-gate step alongside `make test`/`validate`.
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
