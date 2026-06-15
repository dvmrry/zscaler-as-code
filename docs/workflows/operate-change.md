# Workflow — operational config change from a ticket

Turn a fuzzy BAU request ("add `reddit.com` to Blocked Social", "put
`access.corp.example` on the Red Hat app segment") into a reviewed config diff.
This is the high-churn path: URL-category and ZPA app-segment edits.

It is a **workflow, not a script**, and it has two hard rules:

- **The agent never hand-edits config JSON.** Every edit goes through a tested,
  allowlisted primitive (`make url-add` / `url-rm` / `domain-add` / `domain-rm`),
  so a fat-finger can't reshape a structured field.
- **The agent never applies.** It produces a reviewed PR; the normal
  plan → approval → apply path mutates the tenant. A misread ticket becomes a
  diff a human rejects, not a bad apply.

Discovery grounds on **committed config**, not a live API — the config already
mirrors the fetched tenant, so it's deterministic and works without credentials.

## Scope — what's in this fast path

Only allowlisted **additive list-of-strings** edits:

| Resource | Field | Target |
|---|---|---|
| `zia_url_categories` | `urls` | `make url-add` / `url-rm` |
| `zpa_application_segment` | `domain_names` | `make domain-add` / `domain-rm` |

Other list fields (URL-category `keywords` / `db_categorized_urls`, etc.) are a
deliberate add when the need shows up — one `EDITABLE` entry in `tools/operate.py`
+ a thin target + a test — not a loosening into arbitrary edits.

Anything else is **out of this path** — escalate, don't improvise:
- A **new instance** — a brand-new URL category or app segment of a type the
  template already manages — is a new keyed config entry with many required
  fields, not a single-list edit. Author it as a normal config change (or create
  it at the source and `make fetch` it in), then run the same gates + plan + PR.
  Not a primitive, but still this repo's ordinary config flow.
- A **structural change** — ports, policy precedence/order, SSL bypass, account
  settings — is hand-authored with full review; these aren't single-list edits.
- An **unmanaged resource type** — the template doesn't manage that resource
  *type* at all — is the `add-resource` workflow
  (`docs/workflows/add-resource.md`): registry + generator work, a different job
  from editing or adding an instance.

## Phase 1 — Intake

Parse the ticket into a structured request, noting your confidence in each field:

- **area** — URL category or app segment?
- **op** — add or remove?
- **target** — the *display name* as the ticket states it ("Blocked Social").
- **value** — the URL / domain.
- **tenant** — which tenant label?

Don't resolve anything to a config key yet — that's Phase 2, against the config.

## Phase 2 — Resolve + clarify  ·  gate: `make find-key`

The ticket gives a display name; the edit needs the **config key**. Resolve it
against committed config — never eyeball it:

```bash
make find-key TENANT=<label> TYPE=zia_url_categories NAME="Blocked Social"
# -> blocked_social   Blocked Social
```

- **Exactly one match** → use that key.
- **Zero or many matches** → **clarify, don't guess.** Ask a closed-set question
  (the candidate keys + "other"), using the runtime's structured-question
  facility, and echo the choice. Zero matches may mean the category/segment
  doesn't exist yet — that's out of scope (it's a *new* resource, not an edit).
- **Tenant unstated** → clarify it the same way; never assume a default.

Then sanity-check the precondition by reading the current value (the primitive
will report `no-op` if the URL is already present, but confirming up front keeps
the change honest):

```bash
python3 -c "import json; d=json.load(open('config/<tenant>/zia_url_categories.auto.tfvars.json', encoding='utf-8')); print(d['items']['<key>'].get('urls'))"
```

Artifact of this phase: a resolved spec — concrete `tenant`, resource, config
key, field, value, op.

## Phase 3 — Apply the edit via the primitive

Run the tested target — not a hand edit:

```bash
make url-add  TENANT=<label> CATEGORY=<key> URL=<url>
make url-rm   TENANT=<label> CATEGORY=<key> URL=<url>
make domain-add TENANT=<label> SEGMENT=<key> DOMAIN=<domain>
make domain-rm  TENANT=<label> SEGMENT=<key> DOMAIN=<domain>
```

The primitive is idempotent (already-present add / absent remove → `no-op`,
nothing written), refuses an unknown key (listing the real candidates), refuses
any non-allowlisted field, and writes the file in canonical transform form so
the diff is exactly the one line you intended.

## Phase 4 — Gate  ·  `make typecheck` + `make lint` + the plan

```bash
make typecheck TENANT=<label>     # each error line carries its own remediation
make lint TENANT=<label>          # set duplicates, URL/IP syntax, category shadowing
```

Then the **plan** is the proof the change does only what you meant. It runs in
the delivery pipeline on your PR (or locally as `make plan-changed TENANT=<label>
BASE=origin/main` if you have tenant credentials + state). Read it: the plan
must show *only* the one add/remove on the one root — nothing else.

## Phase 5 — PR

Raise the PR with the config diff and the plan summary. A human approves and the
delivery pipeline applies it. **Do not apply.** Note in the PR: the ticket, the
resolved key, and the single edit.

---

## Clarify, don't guess

Ask a closed-set question (and echo the answer) rather than guess at: which
category/segment when `find-key` returns more than one; the tenant when the
ticket doesn't name it; add-vs-remove when the wording is ambiguous. **Never
confirm a default unless the evidence is literally in the ticket or the
`find-key` output.** This is the front end most exposed to fabrication — a
plausible-but-wrong "you probably meant the Social Networking category" edits
the wrong policy.

## Don't fabricate

| Question | Source of truth |
|---|---|
| What config key does this display name map to? | `make find-key` (committed config) |
| Is the URL/domain already present? | the config file / the primitive's `no-op` |
| What will actually change? | the plan (Phase 4) — read it, don't assume |

If a field can't be grounded in the ticket or the config, that's a **blocking
unknown** — clarify it, don't invent it.

## Happy path

```bash
make find-key TENANT=<t> TYPE=zia_url_categories NAME="Blocked Social"   # -> <key>
make url-add TENANT=<t> CATEGORY=<key> URL=<url>
make typecheck TENANT=<t> && make lint TENANT=<t>
# raise the PR; the delivery pipeline plans it for approval
```
