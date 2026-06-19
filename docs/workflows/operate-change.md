# Workflow -- operational config change from a ticket

Turn a fuzzy BAU request ("add `reddit.com` to Blocked Social", "disable the
stale URL-filtering rule", "put `access.corp.example` on the Red Hat app
segment") into a reviewed config diff. This is the high-churn path: allowlisted
single edits to ZIA / ZPA config that already mirrors the fetched tenant.

It is a **workflow, not a script**, and it has two hard rules:

- **The agent never hand-edits config JSON.** Every edit goes through a tested,
  allowlisted primitive (`make url-add` / `keyword-add` / `rule-disable` / ...),
  so a fat-finger can't reshape a structured field or flip the wrong scalar.
- **The agent never applies.** It produces a reviewed DRAFT PR; the normal
  plan -> approval -> apply path mutates the tenant. A misread ticket becomes a
  diff a human rejects, not a bad apply.

Discovery grounds on **committed config**, not a live API -- the config already
mirrors the fetched tenant, so it's deterministic and works without credentials.

This workflow is executed under the harness contract in
`docs/workflows/operate-change.harness.md`. Follow it; do not improvise past a
gate.

## Scope -- what's in this fast path

Two shapes of allowlisted edit: additive **list-of-strings** edits, and
**scalar enable/disable** toggles.

| Resource | Field | Kind | Target |
|---|---|---|---|
| `zia_url_categories` | `urls` | list | `url-add` / `url-rm` |
| `zia_url_categories` | `keywords` | list | `keyword-add` / `keyword-rm` |
| `zia_url_categories` | `ip_ranges` | list | `iprange-add` / `iprange-rm` |
| `zia_location_management` | `ip_addresses` | list | `locip-add` / `locip-rm` |
| `zpa_application_segment` | `domain_names` | list | `domain-add` / `domain-rm` |
| `zia_url_filtering_rules` / `zia_ssl_inspection_rules` / `zia_cloud_app_control_rule` | `state` | scalar enum | `rule-enable` / `rule-disable` |
| `zpa_application_segment` / `zpa_segment_group` | `enabled` | scalar bool | `segment-enable` / `segment-disable` |

The list primitive appends-and-sorts (or removes) one string; the scalar
primitive sets one allowlisted token. Both are idempotent and refuse anything
off the allowlist. Widening either is a deliberate add when the need shows up --
one allowlist entry in `tools/operate.py` + a thin target + a test -- not a
loosening into arbitrary edits.

## Out of scope -- escalate, don't improvise

Name the shape and route it; do NOT work around a refusal.

- A rule's `url_categories` array (references) or **any list-of-references or
  structured field** -- this is NOT a list-of-strings-content edit and NOT a
  scalar toggle. It looks like a list edit but is out of scope; this is the most
  likely mis-route. The primitive refuses it. Route to the operator / hand-author
  with full review.
- **Ports, precedence/order, SSL bypass, account settings** -- structural;
  hand-authored with full review. Not single-edit primitives.
- A **new instance** (a brand-new URL category, app segment, rule, or location
  of a type the template already manages) -- a new keyed config entry with many
  required fields, not a single edit. Author it as a normal config change (or
  create it at the source and `make fetch` it in), then run the same gates +
  plan + PR.
- An **unmanaged resource type** (the template doesn't manage that resource
  *type* at all) -- the `add-resource` workflow
  (`docs/workflows/add-resource.md`): registry + generator work, a different job.

## State / enabled -- clarify, don't guess

"Is the rule already disabled?" and "is this segment already enabled?" are
**config questions, not memory questions**. The source of truth is the config
file (or the primitive's `no-op`: setting a scalar to its current value writes
nothing and reports `no-op`). Read it; never assume the current state.

A `state`/`enabled` toggle changes whether live policy is enforced -- disabling
a URL-filtering or SSL-inspection rule turns enforcement off. Treat it with the
same care as any policy change: resolve the exact key, confirm the current
value, and let the DRAFT PR + human merge be the gate.

## Known dead-end -- unnamed cloud-app-control rules

`zia_cloud_app_control_rule.name` is optional in the schema. An item without a
`name` will NOT surface in `make find-key` (resolve skips items lacking a string
name). If a cloud-app-control rule can't be found by display name, fall back to
listing the config keys for that resource directly and pick the key, then route
the choice through the same gate.

## Phase 1 -- Intake

Parse the ticket into a structured request, noting your confidence in each field:

- **area** -- which resource type (URL category, location, app segment, rule)?
- **field** -- urls / keywords / ip_ranges / ip_addresses / domain_names / state
  / enabled?
- **op** -- add / remove (list), or enable / disable (scalar)?
- **target** -- the *display name* as the ticket states it ("Blocked Social").
- **value** -- the URL / keyword / IP / domain (list edits only).
- **tenant** -- which tenant label?

Don't resolve anything to a config key yet -- that's Phase 2, against the config.

## Phase 2 -- Resolve + clarify -- gate: `make find-key`

The ticket gives a display name; the edit needs the **config key**. Resolve it
against committed config -- never eyeball it:

```bash
make find-key TENANT=<label> TYPE=zia_url_categories NAME="Blocked Social"
# -> blocked_social   Blocked Social
```

- **Exactly one match** -> use that key.
- **Zero or many matches** -> **clarify, don't guess.** Ask a closed-set question
  (the candidate keys + "other"), using the runtime's structured-question
  facility, and echo the choice. Zero matches may mean the resource doesn't exist
  yet -- that's out of scope (it's a *new* resource, not an edit).
- **Tenant unstated** -> clarify it the same way; never assume a default.

Then sanity-check the precondition by reading the current value (the primitive
reports `no-op` if the edit is already in place, but confirming up front keeps
the change honest).

Artifact of this phase: a resolved spec -- concrete `tenant`, resource, config
key, field, value, op.

## Phase 3 -- Apply the edit via the primitive

Run the tested target -- not a hand edit:

```bash
make url-add        TENANT=<label> CATEGORY=<key> URL=<url>
make keyword-add    TENANT=<label> CATEGORY=<key> KEYWORD=<kw>
make iprange-add    TENANT=<label> CATEGORY=<key> IPRANGE=<ip|cidr|a-b>
make locip-add      TENANT=<label> LOCATION=<key> IPADDR=<ip|cidr|a-b>
make domain-add     TENANT=<label> SEGMENT=<key> DOMAIN=<domain>
make rule-disable   TENANT=<label> TYPE=zia_url_filtering_rules RULE=<key>
make segment-disable TENANT=<label> TYPE=zpa_application_segment SEGMENT=<key>
```

(Each `-add` has a matching `-rm`; each `-enable` a matching `-disable`.)

The primitive is idempotent (already-present add / absent remove / same-value
set -> `no-op`, nothing written), refuses an unknown key (listing the real
candidates), refuses any non-allowlisted field or type, and writes the file in
canonical transform form so the diff is exactly the lines you intended.

## Phase 4 -- Gate -- `make typecheck` + `make lint` + the plan

```bash
make typecheck TENANT=<label>     # each error line carries its own remediation
make lint TENANT=<label>          # set duplicates, URL/IP syntax, category shadowing
```

Then the **plan** is the proof the change does only what you meant. It runs in
the delivery pipeline on your PR (or locally as `make plan-changed TENANT=<label>
BASE=origin/main` if you have tenant credentials + state). Read it: the plan
must show *only* the one edit on the one root -- nothing else.

## Phase 5 -- PR

Raise the DRAFT PR with the config diff and the plan summary. A human approves
and the delivery pipeline applies it. **Do not apply.** Note in the PR: the
ticket, the resolved key, and the single edit.

---

## Clarify, don't guess

Ask a closed-set question (and echo the answer) rather than guess at: which
resource when `find-key` returns more than one; the tenant when the ticket
doesn't name it; add-vs-remove or enable-vs-disable when the wording is
ambiguous. **Never confirm a default unless the evidence is literally in the
ticket or the `find-key` output.** This is the front end most exposed to
fabrication -- a plausible-but-wrong "you probably meant the Social Networking
category" edits the wrong policy.

## Don't fabricate

| Question | Source of truth |
|---|---|
| What config key does this display name map to? | `make find-key` (committed config) |
| Is the URL/domain/keyword already present? | the config file / the primitive's `no-op` |
| Is the rule/segment already enabled or disabled? | the config file / the primitive's `no-op` |
| What will actually change? | the plan (Phase 4) -- read it, don't assume |

If a field can't be grounded in the ticket or the config, that's a **blocking
unknown** -- clarify it, don't invent it.

## Happy path

```bash
make find-key TENANT=<t> TYPE=zia_url_categories NAME="Blocked Social"   # -> <key>
make url-add TENANT=<t> CATEGORY=<key> URL=<url>
make typecheck TENANT=<t> && make lint TENANT=<t>
# raise the DRAFT PR; the delivery pipeline plans it for approval
```
