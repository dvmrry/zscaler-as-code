# Workflow — add a resource type

Bring a resource the provider exposes but the template doesn't yet manage under
management. This operationalizes `AGENTS.md` for the most common authoring task.

It is a **workflow, not a script**. Each phase ends at a real `make` gate — a
target that either passes (exit 0) or hands you a remediation line. You exercise
judgment *between* gates; you clarify closed-set choices instead of guessing;
and you never invent provider facts. It assumes a maintainer comfortable reading
the provider schema and `tools/`.

## The model

- **Phased, gate-terminated.** Don't advance past a red gate. The gate's output
  is the authoritative decision table — follow the remediation line it prints.
- **The registry is the single source of truth.** One `tools/registry.json`
  entry wires *both* generation and fetch.
- **`modules/` is generated and committed** (rules 6–7). Change the
  registry / generator / an override and run `make generate` — never hand-edit
  `modules/`.
- **Correctness is auto-verified.** `make conformance` synthesizes adversarial
  API-shaped items for *every* registry type and runs them through the real
  transform + schema + typecheck. A new entry is covered with no hand-written
  fixture.
- **Anti-fabrication.** `schemas/provider/<product>.json` is the truth for which
  fields exist; `make mine` is the truth for how they behave. Never infer a
  field's type, the fetch path, or pagination from memory.

---

## Phase 0 — Select and scope

Pick from the headroom — what the provider offers minus what's already managed:

```bash
python3 - <<'PY'
import json
from tools.registry import generated_types
managed = set(generated_types())
for p in ("zia", "zpa", "zcc"):
    res = set(json.load(open("schemas/provider/%s.json" % p))["resource_schemas"])
    print(p, "headroom:", sorted(res - managed))
PY
```

**Scope gate** (AGENTS.md scope discipline — provider-manageable surface only):
skip `_v2` duplicates of a rule you already manage, account-wide
settings-singletons unless you specifically want one, and identity/admin
resources that live in the IdP. If the resource has dependencies — a firewall
rule needs its IP / service / app-group types; a *derived* resource tracks
another (see `derive_entry`) — do the dependencies first and note the order.

Confirm the type name and product against `schemas/provider/<product>.json`, not
memory.

## Phase 1 — Mine the quirks  ·  gate: `make mine`

Before registering anything, find out what the provider will throw at the
transform:

```bash
make mine        # exits 4 if a NEW quirk has no override coverage (needs network)
```

`make mine` scans the pinned provider Go source for quirks (camelCase keys,
bool-as-int/string, CSV-joined lists, `{id,name}` reference objects,
single-dict-for-list blocks, `max_items=1` oddities, DiffSuppress) and reports
any your overrides don't yet cover. Verify findings against `tools/MINING.md`
(it documents the false-positive lanes). `make surface` is the broader
SDK↔Terraform sweep. **What this surfaces is your override worklist** for
Phases 3 and 5 — knowing it up front beats discovering it as a conformance
failure later.

## Phase 2 — Register

Add one entry to `tools/registry.json`:

```jsonc
"zia_<name>": {
  "generate": true,
  "product": "zia",                                   // zia | zpa | zcc
  "fetch": { "path": "<apiEndpoint>", "pagination": "zia", "query": {} }
}
```

`path`, `pagination`, and any `query` filter come from the SDK / public API docs
— `tools/FETCH.md` documents the pagination shapes. A resource whose config is
**derived** from another's pull (no fetch of its own) takes
`"derive": { "from": "<source_type>", ... }` instead of `fetch` — see
`zpa_policy_access_rule_reorder`, and `docs/workflows/` notes on the
fetch-driven (API→TF) model for why derived resources are planned with their
source, never authored.

## Phase 3 — Generate the module  ·  gate: `make generate`

```bash
make generate        # CHECK=1 is the CI drift gate (rules 6–7)
```

Renders `modules/<type>/` + the tfvars schema from the provider dump. If a quirk
from Phase 1 can't be expressed by the renderer, reach for an override
(`tools/overrides/README.md`):

- a **transform override map** `tools/overrides/<type>.json` — rule maps,
  `import_id`, `acknowledged_drops`; or
- the **escape hatch** `tools/overrides/<type>/main.tf`, used verbatim. Record
  *why* at the top (it is a carried bug) and delete it when upstream fixes land.

Re-run until generation is clean and byte-deterministic.

## Phase 4 — Env root  ·  gate: `make gen-env` + `make check-envs`

```bash
make gen-env TENANT=demo
make check-envs          # fails on any uncommitted env-root drift
```

## Phase 5 — Conform  ·  gate: `make conformance`

```bash
make conformance         # synth -> transform -> typecheck, every registry type
```

The harness auto-covers your new type — there is no fixture to write here.
**Zero mismatches is the contract.** A mismatch prints a remediation line (the
authoritative decision table); the fix is almost always a rule in the override
map. Loop until clean.

## Phase 6 — Demo realism + drop-ack  ·  gate: `make typecheck` + `make lint`

The template ships a fictional `demo` tenant so every gate and golden has data
to run against. Add sample data for the new resource to the demo dataset
(rules 2–4 — fictional, from **public sources only**: SDK structs, provider test
fixtures, API docs; never real responses), exercising the schema's branches.
Copy the shape from an existing `config/demo/<type>.auto.tfvars.json`.

Fields the provider returns but the template won't manage go in the override
map's `acknowledged_drops` — the scope-discipline contract: an *acknowledged*
drop, never a silent omission and never a workaround. The transform flags
unacknowledged drops loudly under `DROPS_CHECK=1` (the bootstrap/refresh path
uses this against real tenants); add anything new it surfaces to
`acknowledged_drops`.

```bash
make typecheck TENANT=demo     # each error line carries its own remediation
make lint TENANT=demo
make demo && make check-demo   # demo dataset stays green
```

## Phase 7 — Pin behavior + verify  ·  gate: `make test` + `make validate`

Rule 8: every behavior change ships a golden fixture.

```bash
make update-goldens      # re-bless generator goldens from current output
# add/extend a transform fixture under tools/tests/ for any NEW transform behavior
make test                # full Python suite
make validate            # terraform fmt / validate (-backend=false)
```

## Phase 8 — PR

Dev branch off `main`, PR — no direct-to-main. In the PR note: the resource
added, any `acknowledged_drops`, and any carried-bug override with its
delete-when condition.

---

## Clarify, don't guess

When a choice has a small closed set, ask (and echo the answer) rather than
guess: which resource / cluster this pass covers; the dependency order; whether
a borderline resource is in-scope or a deliberate drop. **Never confirm a
default unless the evidence is literally in front of you** — the schema, the
ticket, `make mine` output. A fabricated "it's probably X" sends a whole module
down the wrong path before any gate can catch it.

## Don't fabricate provider facts

| Question | Source of truth |
|---|---|
| Does this field exist? What type? | `schemas/provider/<product>.json` |
| How does the API shape it (quirks)? | `make mine` / `make surface` + `tools/MINING.md` |
| Fetch path / pagination? | the SDK + `tools/FETCH.md` |

If none of those answer it, that's a **blocking unknown** — surface it, don't
invent it.

## The happy-path gate sequence

```bash
make mine && make generate && make gen-env TENANT=demo && make check-envs \
  && make conformance && make typecheck TENANT=demo && make lint TENANT=demo \
  && make demo && make check-demo && make test && make validate
```

When that runs clean and the demo config exercises the resource's schema
branches, the resource is managed. The marginal cost is the quirks (Phase 1/5)
and the realism (Phase 6); everything else is a gate doing the work for you.
