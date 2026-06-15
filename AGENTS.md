# Agent Instructions

Invariants for anyone — human or agent — CHANGING this repo (authoring
tooling, fixtures, generators). These are not suggestions. When a change
would violate one, stop and leave a note instead of being clever.

> OPERATING the repo (adopting/reconciling a live tenant: fetch,
> transform, plan, import, commit-back) is a different job with a
> different audience — see `OPERATING.md`. If you are running `make`
> targets against a real tenant rather than editing `tools/`, read that
> first.
>
> ADDING a resource type the provider exposes but the template doesn't yet
> manage? Follow `docs/workflows/add-resource.md` — the phased, gate-by-gate
> workflow that puts these invariants to work.

## Data hygiene

1. No credentials, tokens, or tenant identifiers are ever committed — not
   in code, fixtures, docs, or test data.
2. All fixtures and sample config are fictional, shaped from public
   sources only: `zscaler-sdk-go` / `zscaler-sdk-python` response structs,
   provider test fixtures, public API docs. Never paste real API
   responses.
3. `config/` holds fictional sample data exercising schema branches. Real
   tenant values belong in a private deployment repo, never here.
4. Fixtures derive from public sources only (provider schemas, public SDK
   structs, API docs). Real-world mismatches arrive as written symptom
   descriptions and are fixed here against fictional fixtures — real data
   never enters this repo in any form.

## Code rules

5. Python under `tools/` is stdlib-only with Python 3.6-floor syntax: no
   `match`, no `X | Y` unions, no walrus `:=`, no f-string `=`, no
   `dataclasses`, no pip dependencies at runtime, so it runs in
   restricted enterprise environments. Every text-mode `open()` pins
   `encoding="utf-8"` — the default is locale-dependent and non-UTF-8
   agents mis-read the generated files' em-dashes (enforced by
   tools/tests/test_encoding_discipline.py). `make test-floor` verifies this
   under a real Python 3.6 where Docker is available (optional dev
   check — CI runs `make test` with whatever interpreter the agent has).
6. `modules/` is generated output. Never hand-edit it. Change the generator
   or add an override under `tools/overrides/`, then run `make generate`.
   CI fails on any drift (`make generate CHECK=1`).
7. Generated output is committed and deterministic: same inputs produce
   byte-identical outputs (sorted keys, stable ordering, trailing newline).
8. Every behavior change ships with a golden fixture under `tools/tests/`.
   If a behavior cannot be pinned by a fixture, do not ship it.
9. All entry points are `make` targets. Extend the Makefile rather than
   inventing new invocation paths; CI pipelines stay thin shells over
   `make` (see `pipelines/`).

Prefer boring, explicit code.
