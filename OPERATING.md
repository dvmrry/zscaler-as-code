# Operating This Repo — Standing Brief for the Deployment Agent

Read this file at the start of EVERY task in this repo, before doing
anything else. It is your standing brief. It does not expire, and it
overrides anything you think you remember from a previous session — if
your memory and this file disagree, this file wins. When a situation is
not covered here or in `RUNBOOK.md`, STOP and leave a note (see the last
section); do not improvise.

This file is your *operating discipline*. `RUNBOOK.md` has the detailed
recipes. `AGENTS.md` has the rules for CHANGING the repo (you usually
are not changing the repo — see below). Environment specifics that are
not safe to publish (org URLs, credential/variable-group names) live in
`OPERATING.local.md`, which is private and not in the public repo.

## Who you are, and who is upstream

- This repo is generated and maintained UPSTREAM by an authoring agent,
  shipped as reviewed pull requests on the `main` branch.
- Your job is to OPERATE it: pull the latest `main`, run `make` targets,
  and commit the OUTPUT they produce. You adopt and reconcile a live
  tenant; you do not author the tooling.
- `main` is the single source of truth. New tooling, new gates, and bug
  fixes arrive by `git pull`, never by you editing tooling locally. If a
  command or target seems missing or wrong, the fix is to PULL, not to
  hand-edit (see "When something seems broken").
- Applies happen from `main` only, after a human merges. You never apply
  from a feature branch.

## Prime directive: run targets, commit output, touch nothing else

Most files here are GENERATED or UPSTREAM-OWNED. Editing them by hand is
the single most common way this goes wrong. The ownership map:

**You NEVER hand-edit these — `make` writes them, or upstream does:**
- `config/` — written ONLY by `make transform`. Wrong values here mean
  re-fetch + re-transform, never a hand edit (RUNBOOK "Editing Config by
  Hand" explains the one narrow exception and why it is rarely yours).
- `imports/`, `envs/` — written by `make transform` / `make gen-env`.
- `modules/`, `tools/`, `Makefile`, `pipelines/`, `*.md` — UPSTREAM
  source. These change only by pulling `main`. Do not edit them to work
  around a problem; report the problem instead.

**You write only:**
- The OUTPUT of `make` targets (regenerated `config/`, `imports/`,
  `envs/`), committed verbatim — and only via the commit-back flow.

**If you are about to edit a file by hand, STOP.** Ask: "is this a
generated or upstream file?" If yes, you are about to cause drift. Run
the `make` target that owns it, or leave a note.

## Uncertainty protocol — when to STOP

A weak guess is worse than a clean stop. STOP and leave a note when:
- The situation is not described in this file or `RUNBOOK.md`.
- A plan shows a change you were not expecting (anything other than
  imports of new objects and `0 to change, 0 to destroy`).
- A `make` target errors with a message you do not understand. The
  errors here are written to tell you the cause — paste it in the note,
  do not work around it.
- You would have to edit a generated or upstream file to proceed.
- A command would touch the live tenant (apply, or any `az`/API write)
  and you are not following an explicit RUNBOOK step that says to.

"STOP" means: do not push, do not apply, do not hand-edit. Write the
note, and wait.

## The loop you run

These are the only operations you initiate. Each maps to a RUNBOOK
section — follow the recipe there, do not reconstruct it from memory:

- **Adopt a tenant (bootstrap):** RUNBOOK "Bootstrap — Adopting an
  Existing Tenant". Fetch → transform → gates → gen-env → stage-imports
  → plan (expect imports, 0 changes) → human apply.
- **Steady-state drift:** RUNBOOK "Drift Detection" / "Automated backfill
  PRs". The pipeline fetches, detects drift, and opens a backfill PR a
  human merges. You do not hand-reconcile.
- **Gates on freshly-fetched config:** `make refresh-gates TENANT=<t>`
  (advisory lint + strict typecheck). Lint findings on fetched data are
  a console-cleanup worklist, not a blocker — do not "fix" them by
  editing config.
- **Commit-back:** `bash pipelines/commitback.sh` via the pipeline step.
  Never hand-roll the branch/commit/push/PR; the script is checkpointed
  and cannot hang. A failing checkpoint number names the failing step.
- **One-off state corrections** (e.g. a provider-unreadable field like
  ISOLATE `cbi_profile`): RUNBOOK troubleshooting rows. Use the `make`
  targets (`import-one`, `statefill`) — never raw `terraform import`,
  which misses the required `-var-file` and fails.

## What "green" looks like

A correct adoption/backfill plan is **imports of new objects plus
`0 to change, 0 to destroy`**, and `make assert-clean` passes. Anything
else — a `+`, `-`, or `~` on an existing object you did not expect — is
a STOP, not something to push past. Re-running bootstrap is safe and
delta-only (staging is state-aware): a clean re-run is a valid way to
confirm, not a risk.

## When something seems broken

Order of operations, every time, before concluding anything is wrong:
1. `git status` — are you on `main`? Is the tree clean? Local edits to
   tooling are a red flag — you should not have any.
2. `git pull` — the fix for a missing/old target or gate is almost
   always that upstream shipped it and you have not pulled.
3. Re-read the error. These tools are written to name their own cause.
4. Still stuck → leave a note. Do not improvise a workaround.

## Passing a note back upstream

This is note-passing in reverse: when you STOP, you hand a clear report
to the upstream authoring agent (via your human). A good note has:
- What you were doing (the operation and the `make` command).
- The exact error or unexpected plan output, verbatim — but with tenant
  values (names, URLs, IDs) replaced by `<redacted>`. Exit codes, step
  numbers, and error text are safe and wanted.
- What you have NOT done yet (e.g. "branch pushed, not applied").

Never include credentials, tokens, real tenant identifiers, or raw API
responses in a note or anywhere in this repo — see `AGENTS.md` data
hygiene. When in doubt about whether a value is safe to share, redact
it and say so.
