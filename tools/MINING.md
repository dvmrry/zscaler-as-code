# Quirk mining: the detection pattern

Every behavioral surprise this project has hit in production traced back
to a rule that **already existed, in code, in the provider's Go source**
before the incident. The schema dump describes shapes; the provider's
*runtime* — ValidateFuncs, flatten functions, read normalizations —
describes behavior, and behavior is where the incidents live. This
document is the procedure for finding those rules *before* they find
you. `tools/mine.py` (`make mine`) mechanizes the recurring classes;
the sections below are the full method, written to be executable
without prior context.

The one-sentence version: **don't debug the symptom — read the
provider's read path.** The plan diff, the API rejection, the phantom
drift are all downstream of a `d.Set(...)` or a `ValidateFunc` you can
go read.

## Incident → source-rule ledger

Each of these cost a failed plan, a hung import, or silent wrong data
before the rule was mined. The rule was in the provider source the
whole time:

| Incident | Provider source rule | Override that encodes it |
|---|---|---|
| `size_quota` rejected: "expected 10–100000", API sent 51200000 | `validation.IntBetween(10, 100000)` + `resp.SizeQuota / 1024` (KB→MB) in the read | `divide` + `ranges` |
| "Too many departments blocks" at plan | schema `max_items=1`; flatten merges all IDs into ONE block | none — transform auto-merges `block_is_single` blocks |
| URL filtering rules rejected: missing url_categories | read normalizes empty → `["ANY"]` | `defaults` |
| zpa app segment phantom server_groups drift | `flattenCommonAppServerGroupSimple([]ServerGroup)` returns ONE merged block; schema says N | `merge_blocks` |
| ZCC failopen settings would have applied INVERTED | `boolToInvertedInt` — API speaks 0=enabled | `invert_bool` |
| source_countries perma-diff | `strings.TrimPrefix(c, "COUNTRY_")` in the read | `strip_prefix` |
| predefined/one-click rule import hung 10+ min | provider cannot manage service-owned objects | `skip_if` |
| policy_style bool vs API string enum | read maps `DUAL_POLICY_EVAL`→true, `NONE`→false | `value_map` |
| zpa policy rule diffs inside conditions[].operands | `operands.name` is Computed+Optional — the API rewrites it to the referenced object's display name (provider issue #287: "remove name from your operands"); nested `microtenant_id` "0" stub | dotted `drops` / `drop_if_default` (`conditions.operands.name`) |
| ZPA plans show `&amp;`/`&gt;` updates on name/description | the Go SDK HTML-unescapes every ZPA and ZCC response — top-level name/description only, applied TWICE (`unescapeHTML` in zscaler-sdk-go `zscaler/utils.go`, called from `zparequests.go`/`zccrequests.go`; zia has no such call) — so state holds literals while the raw API carries entities | transform mirrors it (`_unescape_html_fields`); `make lint` warns on residual entities |
| zia_url_categories drift PRs churn with no-op `urls` reorders | `suppressURLCategoriesReorderDiff` treats `urls` as a SET at plan time despite the TypeList schema — order is meaningless but the API returns it unstably (found by auditing the miner's suppressed DiffSuppressFunc lane, `MINE_VERBOSE=1`) | `sort_lists` |
| uppercase domain_names perma-diff (hand-edit only) | the ZPA API lowercases `domain_names` on response (provider troubleshooting guide — same documented-normalization class as unescapeHTML) | `make lint` case warn (`domain_names` in URL_ENTRY_FIELDS) |
| ZPA app connector group updates 400 naming `signingCertId` | TENANT-side rollout (OAuth2 connector enrollment) made a field required mid-adoption with zero provider/pin change; the API speaks `signingCertId`, the schema speaks `enrollment_cert_id`, and the provider auto-resolves it on CREATE only (provider issue #650 — found via the issue-tracker lane; the drop report had been flagging the unknown `signing_cert_id` field all along) | `renames` |

## The mechanical lanes (`make mine`)

`tools/mine.py` fetches the EXACT pinned provider tags (the pins in
`tools/schema-extract/main.tf` — the same source `make schemas` dumps),
runs a regex battery over each generated resource's Go file plus the
shared helper files, and compares every hit against override coverage.
One line per finding; `[MISSING]` lines name the override key to add.
Exit 0 = covered; exit 4 = new missing coverage (make flattens it to 2
— a red run is the signal); needs network like `make fetch`.

| Class | Go idiom (SDKv2: zia/zpa) | Go idiom (plugin framework: zcc) | Override |
|---|---|---|---|
| `range_validator` | `validation.IntBetween(a, b)` | `int64validator.Between(a, b)` | `ranges` (enforced by `make lint`) |
| `enum_validator` | `validation.StringInSlice([...])` | `stringvalidator.OneOf(...)` | informational (enum lint is backlog; print with `MINE_VERBOSE=1`) |
| `unit_conversion` | `resp.Field / 1024` in the read | — | `divide` |
| `literal_default` | `if len(resp.X) == 0 { d.Set("x", []string{"ANY"}) }` | — | `defaults` |
| `int_bool_inverted` | `boolToInvertedInt(d.Get("x").(bool))` | `boolToInvertedStr(plan.X.ValueBool())` / `invertedIntToBool(p.X)` | `invert_bool` |
| `strip_prefix` | `strings.TrimPrefix(v, "COUNTRY_")` | — | `strip_prefix` |
| `merge_flatten` | slice-param helper that returns `[]interface{}{map{...}}` | — | `merge_blocks` (schema-aware, see below) |
| `diff_suppress` | `DiffSuppressFunc: name` | — | informational (`MINE_VERBOSE=1` to print) — READ each named func after a bump; suppress bodies are where normalizations hide (this lane surfaced the `urls`-is-a-set finding) |

Source layout per dialect (encoded in `LAYOUTS` in `tools/mine.py`):

- **SDKv2** (zia, zpa): `<product>/resource_<type>.go`, shared helpers in
  `<product>/common.go` (+ zia `utils.go`, `validator.go`). Files that
  break the naming convention go in `FILE_ALIASES` (e.g.
  `zpa_application_server` lives in `resource_zpa_app_server_controller.go`).
- **Plugin framework** (zcc): `internal/framework/resources/<name-without-
  product-prefix>.go`. Different validator imports, model structs with
  `tfsdk:` tags instead of schema maps, `plan.X.ValueBool()` accessors.

## Verifying a MISSING line (the per-class procedure)

The battery's field attribution is *nearest-preceding-declaration* — a
heuristic. **Never encode an override from a MINE line alone.** For
each `[MISSING]`:

1. Open the provider file at the pinned tag:
   `https://github.com/zscaler/terraform-provider-<product>/blob/v<pin>/<path>`
   (pins: `tools/schema-extract/main.tf`).
2. Find the matched idiom (the MINE line prints the matched text) and
   confirm which schema field it actually belongs to.
3. Class-specific check:
   - **ranges**: confirm the bounds and the UNIT. If the read also
     divides (`unit_conversion` hit on the same field), the range is in
     the *config* unit, not the API unit — `size_quota` is 10–100000
     **MB** in config while the API speaks KB.
   - **unit_conversion**: confirm direction — `divide` in the override
     is API→config; the provider's write path multiplies back.
   - **literal_default**: use the EXACT literal the provider sets, or
     the round-trip never stabilizes.
   - **invert_bool**: list the snake_case field names. Both directions
     in the source (`boolToInverted*` on write, `inverted*ToBool` on
     read) refer to the same field set; one override entry covers both.
   - **strip_prefix**: confirm the write path re-adds the prefix
     (otherwise it's a one-way transform, not a round-trip rule).
   - **merge_flatten**: see the false-positive section below — check
     the helper's PARAMETER and the schema both.
4. Add the override entry to `tools/overrides/<type>.json`
   (key reference: `tools/overrides/README.md`).
5. Add an e2e test to `QuirkClosureTest` in
   `tools/tests/test_transform.py`: raw API-shaped input in, asserted
   config shape out. Use the REAL API shape (check `pulls/` or the
   demo cassette), not a guessed one.
6. Re-run `make mine` — the line must flip to `[covered]` — then
   `make test && make transform TENANT=<label> && make typecheck
   TENANT=<label> && make lint TENANT=<label>`.

If a finding is real but deliberately not encoded (triaged
low-priority), bless it: `UPDATE_BASELINE=1 make mine` writes
`tools/overrides/mine-baseline.json`; baselined findings still print
but stop failing the run. The baseline is `acknowledged_drops` for the
miner — commit it with a PR note saying *why* each entry is parked.

## False positives we've already hit (read before trusting a hit)

These are the known traps — each one burned an hour live:

- **Singleton return ≠ merge.** `flattenCommonZPNERIDSimple(credential
  *common.ZPNERID)` and `flattenCustomIDSet(customID *common.IDCustom)`
  return one block because they take ONE struct — one-in-one-out. The
  merge quirk needs a **slice parameter** (`[]T`): N elements in, one
  block out. The battery now checks the signature; if you're mining by
  hand, you must too.
- **Schema-single blocks auto-merge.** ZIA ID-group blocks
  (`departments`, `groups`, `users`, …) are `max_items=1` in the schema
  — the transform already merges them via the shared `block_is_single`
  predicate. A merge-shaped flatten on such a field needs NO override.
  `merge_blocks` exists only for blocks where the schema says N but the
  flatten returns 1 (zpa `server_groups`).
- **Schema strings holding numbers.** zpa latitude/longitude are
  `IntBetween`-style validated but typed STRING in the schema.
  `make lint`'s range check parses numeric strings for exactly this;
  don't "fix" the type in config.
- **Field attribution drift.** The nearest-preceding-field heuristic
  mislabels when validators sit in nested blocks or shared schema
  funcs. Step 2 above is not optional.
- **Nested flattens are invisible to the battery.** The `d.Set` lane
  only sees top-level flattens; helpers invoked INSIDE another flatten
  (operands inside `flattenPolicyConditions`) never appear. When a
  resource has nested blocks, read the whole flatten chain by hand.
- **Computed+Optional attributes inside nested blocks are drift
  surfaces.** Anything the schema marks computed can be rewritten by
  the API (operand `name` becomes the referenced object's display
  name). If config carries it, it is compared; if the API rewrites it,
  it never round-trips. Drop such fields with a dotted `drops` path
  unless they are required on write (operand `idp_id` IS sent on
  write — keep it).
- **Ordered schema, unordered backend.** zpa policy `conditions`/
  `operands` are `TypeList` (positional compare) while the backend
  treats them as an unordered grouped structure — that mismatch is WHY
  the provider's v2 policy resources moved to `TypeSet` with operands
  grouped by `object_type`. No override can fix ordering; if the API
  returns a different order than config captured, the drift pipeline
  is the reconciliation (re-fetch adopts the API's current order).
  Related trap: v1 `rhs_list` is Computed but never set by the read —
  config using it can never converge; always use per-value `rhs`
  operands (which is what the transform emits).

## The non-mechanical lanes

Cross-referencing surfaces what no single repo states. When a resource
misbehaves and `make mine` is silent, work through these in order —
each is "read the source someone already wrote", just in a different
repo:

1. **Provider Go source beyond the battery** — the resource's
   `Create`/`Update` functions (write-path payload pruning, computed
   fields silently dropped), `customizeDiff` functions, and any helper
   the read calls that the battery's shapes don't cover. Grep the
   field's Go name (CamelCase) across the provider repo.
2. **Go SDK** (`zscaler-sdk-go`): the service struct for the resource.
   Struct tags tell you the wire names; field TYPES tell you tri-state
   semantics (`*bool`/`*int` = absent-vs-zero matters — the provider
   may be hiding a default). The SDK is also where response envelopes
   live (pagination, wrappers) when fetch output looks truncated.
   Crucially, the SDK's REQUEST PATH can rewrite every response before
   the provider sees it — worked example: `unescapeHTML` in
   `zscaler/utils.go` HTML-unescapes top-level name/description (twice)
   on every ZPA and ZCC response, which is why raw-API pulls carry
   `&amp;` where provider state has `&`. The miner does not scan the
   SDK repo; when a value differs between a pull and provider state
   with NO provider-source explanation, grep the SDK's
   `*requests.go`/`utils.go` next. Pin: the provider's `go.mod` names
   the exact SDK tag.
3. **Python SDK** (`zscaler-sdk-python`): the docstrings frequently
   state UNITS, valid enums, and value meanings that appear nowhere in
   the Go code. Grep the snake_case field name. Treat it as
   documentation, not ground truth — when it disagrees with the
   provider Go source, the provider wins (it's what terraform runs).
4. **Ansible collections** (`zscaler.ziacloud` / `zscaler.zpacloud`):
   each module's `argument_spec` declares `choices=`, `required=`, and
   defaults — an independent second opinion on enums and required
   fields, maintained by the same vendor. Disagreement between the
   argspec and the provider schema is itself a signal: someone encoded
   a constraint the other repo forgot.
5. **API semantics no repo encodes**: ordering is insert-shift (a rule
   insert cascades order changes to all neighbors), ZIA URL matching is
   most-specific-wins (cross-category shadowing), predefined/one-click
   objects are service-owned and unmanageable, referential deletes are
   blocked by anchoring (ZPA IP anchoring). These surface only in
   vendor docs and incidents. Encode them where they fit: `skip_if`
   for unmanageable objects, `make lint` checks for config-visible
   hazards (shadowing, order collisions), RUNBOOK troubleshooting rows
   for operational ones.

## Worked example: size_quota, end to end

1. **Symptom**: plan rejects `size_quota = 51200000` — "expected
   size_quota to be in the range (10 - 100000)". Config was built from
   the API value, so how is it out of range?
2. **Mine**: `resource_zia_url_filtering_rules.go` carries BOTH
   `validation.IntBetween(10, 100000)` on `size_quota` AND
   `resp.SizeQuota / 1024` in the read. Two rules, one field: the API
   speaks KB, config speaks MB, the provider converts internally.
3. **Encode**: `"divide": {"size_quota": 1024}` (API→config) +
   `"ranges": {"size_quota": [10, 100000]}` (so `make lint` catches
   hand-edited out-of-range values at PR time, before any plan runs).
4. **Test**: e2e in `QuirkClosureTest` — raw item with the KB value in,
   MB value out; lint test with an out-of-range value in, ERROR out.
5. **Verify**: `make mine` shows both lines `[covered]`; `make test`,
   re-transform, plan goes green.

The pattern generalizes: *symptom → read the provider's read path and
validators → encode as override data (never transform code) → e2e test
with real API shapes → re-mine to confirm coverage.*

## When to run

- **Every provider bump** — RUNBOOK "Provider Bumps" step 5. New
  provider code = new rules; the miner diffs them against coverage
  automatically.
- **Every new resource adoption** — before first import, not after the
  first failed plan.
- **Any unexplained plan/apply mismatch** — if the API value and the
  config value disagree and you can't say why, the answer is almost
  certainly a `d.Set` away.
