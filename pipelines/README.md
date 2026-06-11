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
| **Validation** (PR gate) | every PR | none | never touched (`-backend=false` everywhere) | `test`, `validate`, `typecheck`, `test-envs`, `validate-imports` |
| **Plan → Apply** (delivery) | merge / manual | real API creds + state auth | locked during plan/apply | `plan-changed SAVE=1` → approval → `apply` |
| **Drift** (scheduled) | cron | read-only API creds | not used | `drift` (exit 3 = drift; open a PR with the diff) |

Notes that apply to every platform:

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
