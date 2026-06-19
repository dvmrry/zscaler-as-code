---
role: operator-agent
artifact: outputs/operate/<slug>/
title: operate-change harness contract
sources:
  - docs/workflows/operate-change.md
  - tools/operate.py
  - tools/opgate.py
---

# operate-change -- harness contract

This is the execution contract for `docs/workflows/operate-change.md`. The
workflow doc explains *why*; this doc fixes *exactly what command to run at each
phase*, *which artifact gates the next phase*, and *when to halt*. Follow it
literally. The command shapes below are deterministic on purpose -- run them as
written, fill only the `<...>` placeholders, and read the answer back from the
artifact rather than from memory.

Two invariants override everything:

1. **The gates are load-bearing.** Never run a phase whose prior artifact is
   absent or `blocked`. Never narrate a gate as passed before its helper command
   exits 0.
2. **Never apply. DRAFT PR only.** No `terraform apply`, ever.

## Phase chain

Artifacts live under `outputs/operate/<slug>/`, where `<slug>` is
`yyyy-mm-dd-<symptom>` (regex `^[0-9]{4}-[0-9]{2}-[0-9]{2}-[A-Za-z0-9_-]+$`),
coined once at intake.

```
P1 Intake     (no gate)    -> 00-intake.json    make op-intake ...
P2 Resolve    GATE G1      -> 01-resolve.json    make find-key ... then make op-resolve ...
P3 Apply      (prose loop)  -> 02-apply.json     make <primitive> ... (record one-line results)
P4 Validate   GATE G2      -> 03-validate.json   make op-validate ...
P5 PR         GATE G3      -> 04-pr.json         commitback checkpoint, DRAFT PR
```

Two helper-enforced gates -- **G1 Resolve** and **G2 Validate** (op-resolve /
op-validate write a `blocked` artifact and exit nonzero). **G3 PR** is an
agent-performed checkpoint, NOT a tooling gate: the agent opens the DRAFT PR via
commitback. Intake and Apply are prose phases (no helper-enforced gate); each
still writes its artifact so `status` can resume.

Each phase's input is the prior phase's `pass` artifact. Before starting any
phase, confirm the prior artifact exists and is `pass`. If it is absent or
`blocked`:

```
Prior phase not pass -- cannot proceed
```

then run and read:

```bash
make op-status SLUG=<slug>
```

`make op-status` is the single resume entry point -- it prints the latest
`phase`, `status`, and `next_step`. Trust it over any recollection of where you
were.

## P1 Intake (no gate) -> 00-intake.json

Coin the slug, capture the structured request, write the intake artifact.

```bash
make op-intake SLUG=<yyyy-mm-dd-symptom> TENANT=<t> INTAKE=<path-to-intake.json>
```

`op-intake` validates the slug regex and the tenant, then records the parsed
targets. Status is always `pass` (this is structured capture, not a gate). If the
slug or tenant is rejected, fix it and rerun -- do not advance.

## P2 Resolve -- GATE G1 -> 01-resolve.json

First resolve each display name to a config key:

```bash
make find-key TENANT=<t> TYPE=<resource_type> NAME="<display name>"
```

Then run the gate, which resolves every target and enforces exactly-one-match:

```bash
make op-resolve SLUG=<slug> TENANT=<t> TARGETS=<path-to-targets.json>
```

- Each target resolving to **exactly one** config key -> `pass`; the helper
  records the `config_key`.
- **Zero or many** matches for any target -> `blocked`; the blocking issue lists
  the candidates (or "no match"). HALT. Clarify the ambiguity with a single
  closed-set question (candidate keys + "other"), update the targets, rerun.

Do not hand-pick a key past a `blocked` resolve.

## P3 Apply (prose loop) -> 02-apply.json

Only after `01-resolve.json` is `pass`. Run one allowlisted primitive per
resolved target. Use the literal command for the field:

```bash
make url-add        TENANT=<t> CATEGORY=<key> URL=<url>
make keyword-add    TENANT=<t> CATEGORY=<key> KEYWORD=<kw>
make iprange-add    TENANT=<t> CATEGORY=<key> IPRANGE=<cidr>
make locip-add      TENANT=<t> LOCATION=<key> IPADDR=<ip>
make domain-add     TENANT=<t> SEGMENT=<key> DOMAIN=<domain>
make rule-disable   TENANT=<t> TYPE=zia_url_filtering_rules RULE=<key>
make segment-disable TENANT=<t> TYPE=zpa_application_segment SEGMENT=<key>
```

(Each `-add` has a matching `-rm`; each `-disable` a matching `-enable`. For
scalar TYPE values see the scope table in the workflow doc.)

Record each primitive's one-line result (the `set`/`add`/`no-op` line it prints)
into `02-apply.json`. The primitive's idempotence and allowlist are Apply's
safety -- there is no separate gate here. If a primitive **refuses** (unknown
key, non-allowlisted field/type, out-of-scope shape), HALT: this is an
out-of-scope edit. Stop and route it to the operator. Do NOT work around the
refusal.

## P4 Validate -- GATE G2 -> 03-validate.json

```bash
make op-validate SLUG=<slug> TENANT=<t>
```

This runs `make typecheck` then `make lint`. Both exit 0 -> `pass`. Any non-zero
-> `blocked`, with the failing command's tail in the blocking issues. HALT on
`blocked`: read the tail, fix the cause, rerun. Do not proceed to PR on a
`blocked` validate.

## P5 PR -- GATE G3 -> 04-pr.json

Only after `03-validate.json` is `pass`. Bind to the existing commitback
entrypoint:

```bash
pipelines/commitback.sh
```

Before opening the PR, verify ALL of:

1. `03-validate.json` is `pass`.
2. The current branch is a feature branch, **not `main`**.
3. The diff is **only** the intended edits (the lines from P3) -- nothing else.

Then open a **DRAFT** PR with the ticket, the resolved key(s), and the single
edit(s). **Never `terraform apply`.** A human reviews and merges; the delivery
pipeline applies.

## Halt rules (read every time)

- **One clarification per turn.** Ask a single closed-set question, wait for the
  answer, echo it, then proceed.
- **Answer from artifact.** At every gate, read the result from the written
  artifact, not from memory. `make op-status SLUG=<slug>` is the resume entry.
- **A gate that prints its checkpoint before the helper exits 0 is a failed
  transition.** Do not record a phase as passed until its helper returns exit 0.
- **Out-of-scope shape -> stop and route.** A refusal from the primitive, or a
  list-of-references / structural field, means hand-author with the operator --
  never work around it.
- **Never apply, draft only.** No `terraform apply` at any phase. This is a
  POLICY, not a tooling lock -- nothing here intercepts a shell `make apply`;
  the backstops are `make apply`'s own guards plus the DRAFT-PR + human-merge
  boundary, which is the sole safety for a `state`/`enabled` toggle that turns
  live policy enforcement on or off.
