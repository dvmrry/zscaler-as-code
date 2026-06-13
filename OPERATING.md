# Operating This Repo

This is the operating discipline for *running* this repo against a live
Zscaler tenant — adopting it, planning, importing, reconciling drift — as
distinct from *developing* the tooling. `RUNBOOK.md` has the step-by-step
recipes; `AGENTS.md` has the rules for changing the repo itself. When a
situation is not covered here or in `RUNBOOK.md`, stop and leave a note
(see the last section) rather than improvising.

If an automated agent operates this repo, treat this file as a standing
brief: read it at the start of each session, and let it override anything
carried over from a previous one — if your memory and this file disagree,
this file wins.

Environment specifics that should not be published (org URLs, credential
or variable-group names, tenant labels) belong in `OPERATING.local.md`,
which is gitignored and lives only in your private deployment copy. A
template for it is `OPERATING.local.md.example`.

## Operating vs developing

`main` is the source of truth. The tooling — generators, `make` targets,
modules, pipelines — changes only through reviewed pull requests and
reaches you by `git pull`, never by editing it mid-operation. Operating
the repo means: pull `main`, run `make` targets, and commit the OUTPUT
they produce.

- If a target or gate seems missing or wrong, the fix is almost always to
  `git pull` — the change shipped and your checkout is behind — not to
  hand-edit (see "When something seems broken").
- Deployment-specific `make` targets or variable overrides go in
  `local.mk` (auto-included, not shipped by the template), never by
  editing the `Makefile`.
- Applies happen from `main` only, after a human merges.

## Prime directive: run targets, commit output, touch nothing else

Most files here are GENERATED or SOURCE-controlled. Hand-editing them
during operation is the single most common way this goes wrong:

**Never hand-edit these while operating — `make` writes them, or they
change only through a PR:**
- `config/` — written ONLY by `make transform`. Wrong values here mean
  re-fetch + re-transform, never a hand edit (`RUNBOOK.md` "Editing Config
  by Hand" covers the one narrow exception).
- `imports/`, `envs/` — written by `make transform` / `make gen-env`.
- `modules/`, `tools/`, `Makefile`, `pipelines/`, `*.md` — source. These
  change through a PR (development), not to work around an operational
  problem. If one seems wrong, report it rather than patching it in place.

**You write only:** the OUTPUT of `make` targets (regenerated `config/`,
`imports/`, `envs/`), committed verbatim via the commit-back flow.

**About to hand-edit a file? Stop.** Ask whether it is generated or
source. If it is, you are about to cause drift — run the `make` target
that owns it, or leave a note.

## Uncertainty protocol — when to STOP

A wrong guess is worse than a clean stop. STOP and leave a note when:
- The situation is not described here or in `RUNBOOK.md`.
- A plan shows a change you did not expect (anything other than imports
  of new objects and `0 to change, 0 to destroy`).
- A `make` target errors with a message you do not understand. These
  tools are written to name their own cause — capture it in the note;
  do not work around it.
- Proceeding would require hand-editing a generated or source file.
- A command would touch the live tenant (an apply, or any API write) and
  you are not following an explicit `RUNBOOK.md` step that says to.

"STOP" means: do not push, do not apply, do not hand-edit. Write the
note, and wait.

## The loop you run

These are the operations to initiate; each maps to a `RUNBOOK.md` section
— follow the recipe there rather than reconstructing it from memory:

- **Adopt a tenant (bootstrap):** "Bootstrap — Adopting an Existing
  Tenant". Fetch → transform → gates → gen-env → stage-imports → plan
  (expect imports, 0 changes) → human-approved apply.
- **Steady-state drift:** "Drift Detection" / "Automated backfill PRs".
  The pipeline fetches, detects drift, and opens a backfill PR a human
  merges — not a hand-reconcile.
- **Gates on freshly-fetched config:** `make refresh-gates TENANT=<t>`
  (advisory lint + strict typecheck). Lint findings on fetched data are a
  console-cleanup worklist, not a blocker — do not "fix" them by editing
  config.
- **Commit-back:** `bash pipelines/commitback.sh` via the pipeline step.
  Do not hand-roll the branch/commit/push/PR; the script is checkpointed
  and cannot hang, and the last `[commit-back N/5]` line names the failing
  step.
- **One-off state corrections** (e.g. a provider-unreadable field like the
  ISOLATE-rule `cbi_profile`): the `RUNBOOK.md` troubleshooting rows. Use
  the `make` targets (`import-one`, `statefill`) — never raw
  `terraform import`, which omits the required `-var-file` and fails.

## What "green" looks like

A correct adoption or backfill plan is **imports of new objects plus
`0 to change, 0 to destroy`**, with `make assert-clean` passing. Anything
else — an unexpected `+`, `-`, or `~` on an existing object — is a STOP,
not something to push past. Re-running bootstrap is safe and delta-only
(staging is state-aware), so a clean re-run is a valid way to confirm.

## When something seems broken

Order of operations, every time, before concluding anything is wrong:
1. `git status` — are you on `main`? Is the tree clean? Local edits to
   tooling are a red flag.
2. `git pull` — a missing or stale target/gate is almost always a change
   that shipped while your checkout was behind.
3. Re-read the error. These tools are written to name their own cause.
4. Still stuck → leave a note; do not improvise a workaround.

## When you stop: leave a clear report

When you STOP, hand a clear report to whoever continues the work. A good
note has:
- What you were doing — the operation and the exact `make` command.
- The exact error or unexpected plan output, verbatim, with tenant values
  (names, URLs, IDs) replaced by `<redacted>`. Exit codes, step numbers,
  and error text are safe and wanted.
- What you have NOT done yet (e.g. "branch pushed, not applied").

Never include credentials, tokens, real tenant identifiers, or raw API
responses in a note or anywhere in this repo — see `AGENTS.md` data
hygiene. When unsure whether a value is safe to share, redact it and say
so.
