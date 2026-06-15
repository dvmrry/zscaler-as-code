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
  API-shaped items for every *non-derived* registry type and runs them through
  the real transform + schema + typecheck — a new entry is covered with no
  hand-written fixture. (Derived types are skipped here; they're covered by
  their derive-transform and module tests instead — see Phase 4.)
- **Anti-fabrication.** `schemas/provider/<product>.json` is the truth for which
  fields exist; `make mine` is the truth for how they behave. Never infer a
  field's type, the fetch path, or pagination from memory.

Order matters in one place: the **demo data must exist before `gen-env`**, because
`gen_env` bakes the env root's config-backed smoke test from whether
`config/demo/<type>.auto.tfvars.json` exists *at generation time*. The phases
below are sequenced for that.

One gate class is **post-commit, not authoring**: `make check-demo` and
`make check-envs` regenerate and then fail on any `git status` difference — so
for a brand-new resource they correctly report your new, uncommitted files as
"drift" and will **fail until you commit**. They're the clean-tree CI check that
committed == regenerated, not a mid-authoring gate. Run them after the PR commit
(Phase 8) / let CI run them; the authoring signal is `demo` / `typecheck` /
`lint` / `test` passing.

---

## Phase 0 — Select and scope

Pick from the headroom — what the provider offers minus what's already managed:

```bash
python3 - <<'PY'
import json
from tools.registry import generated_types
managed = set(generated_types())
for p in ("zia", "zpa", "zcc"):
    with open("schemas/provider/%s.json" % p, encoding="utf-8") as fh:
        res = set(json.load(fh)["resource_schemas"])
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
Phases 3 and 4.

`make mine` also reports an **unfetchable** count — heed it. The fetcher only
does GET-style paginated endpoints; a resource the SDK lists via a `POST
…/search` (e.g. `zpa_tag_namespace`) isn't fetchable as-is, so confirm a clean
GET endpoint in Phase 2 before committing to it — verify the *method*, not just
the path.

## Phase 2 — Register

Add one entry to `tools/registry.json`. It is strict JSON — no comments:

```json
"zia_<name>": {
  "generate": true,
  "product": "zia",
  "fetch": { "path": "<apiEndpoint>", "pagination": "zia", "query": {} }
}
```

`product` is `zia`, `zpa`, or `zcc`. `path`, `pagination`, and any `query` filter
come from the SDK / public API docs — `tools/FETCH.md` documents the pagination
shapes; confirm the endpoint is a GET (not a `POST …/search`, per Phase 1). A
resource whose config is **derived** from another's pull (no fetch of its own)
takes `"derive": { "from": "<source_type>", ... }` instead of `fetch` — see
`zpa_policy_access_rule_reorder`. A derived resource is planned alongside its
source and is never hand-authored, so it has no demo fixture of its own (Phase 5).

Adding the entry will fail **`tools/tests/test_registry.py`** — it pins the
sorted `generated_types()` list. Add your type to that list (the failure names
the expected value); `make test` in Phase 7 is the backstop if you forget.

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

## Phase 4 — Conform  ·  gate: `make conformance`

```bash
make conformance         # synth -> transform -> typecheck, every non-derived type
```

The harness auto-covers a **non-derived** type — there is no fixture to write
here, and it needs no demo data. **Zero mismatches is the contract.** A mismatch
prints a remediation line (the authoritative decision table); the fix is almost
always a rule in the override map. Loop with Phase 3 until clean.

`make conformance` **skips derived types** — they have no fetch, so there is no
synthesize → transform → typecheck round-trip to run. A derived resource is
verified instead by: its derive-transform unit test (e.g.
`tools/tests/test_transform_reorder.py`); its **source** type's demo fixture, which
`make demo` derives the config from (so a derived type adds no fixture of its own
in Phase 5); and the env smoke test in Phase 6.

## Phase 5 — Demo data + drop-ack  ·  gate: `make typecheck` + `make lint`

The fictional `demo` tenant gives every gate and golden real data to run against.
**The source is a synthetic API-shaped pull**, not the committed config:

1. Add `tools/tests/fixtures/demo/<type>.json` — a fictional pull exercising the
   schema's branches (rules 2–4: public sources only — SDK structs, provider test
   fixtures, API docs; never real responses). *(A derived type has no fixture of
   its own; `make demo` reads its source type's pull.)*
2. Surface fields the provider returns but the template won't manage, and
   acknowledge them — the scope-discipline contract (an explicit drop, never a
   silent omission or a workaround):

   ```bash
   DROPS_CHECK=1 make transform IN=tools/tests/fixtures/demo TENANT=demo RESOURCE=<type>
   ```
   Add anything it flags to the override map's `acknowledged_drops`, then re-run
   until clean. (`make triage IN=… APPLY=1` can bulk-classify drops, but it is
   **global** — `APPLY=1` writes `acknowledged_drops` for *every* type in `IN`,
   including unrelated ones. For a single new resource prefer the manual ack
   above, or review its full diff before committing. A `SYNONYM`/`UNKNOWN`
   finding — e.g. `file_type_id` vs `file_id` — is `triage` correctly forcing
   human review: confirm against the provider read/expand before acknowledging.)
3. Materialize and check:

   ```bash
   make demo                       # transforms the fixtures -> config/demo + imports/demo
   make typecheck TENANT=demo      # each error line carries its own remediation
   make lint TENANT=demo
   make update-demo-goldens        # re-bless tools/tests/fixtures/demo-expected/
   # make check-demo runs in Phase 8 (post-commit) — it git-checks clean-tree
   ```

`config/demo/<type>.auto.tfvars.json` is *generated output* of `make demo` — never
hand-edit it; edit the fixture and re-materialize.

## Phase 6 — Env root  ·  gate: `make gen-env` + `make test-envs`

Run this **after** Phase 5 — the demo config must exist so the env root's smoke
test is config-backed:

```bash
make gen-env TENANT=demo
make test-envs TENANT=demo RESOURCE=<type>   # smoke JUST the new root (RESOURCE scopes it)
# make check-envs runs in Phase 8 (post-commit) — it git-checks clean-tree
```

Scope `test-envs`/`test-modules` with `RESOURCE=<type>` so you smoke only the new
root, not every tenant root — the unscoped loop is slow and, on a constrained box,
can fail later on unrelated roots (provider install / disk) after yours already
passed.

## Phase 7 — Pin behavior + verify  ·  gate: `make test`

Rule 8: every behavior change ships a golden fixture.

```bash
make update-goldens                 # re-bless generator goldens from current output
# add/extend a transform fixture under tools/tests/fixtures/transform/<type>/ for NEW transform behavior
make test                           # full Python suite (includes the demo-pipeline test)
make test-modules RESOURCE=<type>   # mock-provider terraform test for the new module
make validate-imports TENANT=demo   # fixture import addresses resolve against the roots
make validate                       # terraform fmt -check only (NOT terraform validate)
```

`make validate` is formatting only; the real terraform-level validation is
`make test-modules` / `make test-envs` / `make validate-imports`.

## Phase 8 — PR

Dev branch off `main`, PR — no direct-to-main. In the PR note: the resource
added, any `acknowledged_drops`, and any carried-bug override with its
delete-when condition.

After committing, run the clean-tree drift gates — they pass now that your new
files are committed (pre-commit they correctly reported them as drift):

```bash
make check-demo     # committed config/demo == pipeline output
make check-envs     # committed envs == generator output
```

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

Authoring (clean-tree drift gates `check-demo`/`check-envs` are NOT here — they
run post-commit in Phase 8, since they'd flag your new uncommitted files):

```bash
make mine && make generate && make conformance \
  && DROPS_CHECK=1 make transform IN=tools/tests/fixtures/demo TENANT=demo RESOURCE=<type> \
  && make demo && make typecheck TENANT=demo && make lint TENANT=demo \
  && make update-demo-goldens \
  && make gen-env TENANT=demo && make test-envs TENANT=demo RESOURCE=<type> \
  && make update-goldens && make test && make test-modules RESOURCE=<type> \
  && make validate-imports TENANT=demo && make validate
```

Then `git add` + commit, and run `make check-demo && make check-envs` (Phase 8).

The marginal cost is the quirks (Phase 1/4) and the realism (Phase 5);
everything else is a gate doing the work for you.
