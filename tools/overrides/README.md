# Generator overrides

Run `make typecheck TENANT=<label>` after every `make transform` to catch type
mismatches before Terraform does. Each output line ends with a one-line
remediation — follow that suggestion exactly; it is the authoritative decision
table for every known mismatch class.

If `tools/overrides/<resource_type>/main.tf` exists, `make generate` uses
it verbatim instead of the rendered `main.tf` for that resource — the
escape hatch for provider quirks the generator cannot express. Each
override is a carried bug: record why in a comment at the top of the
file, and delete the override (then regenerate) when upstream fixes land.

## Transform override maps

`tools/overrides/<resource_type>.json` configures the transform for that
resource (all keys optional): `key_field` (map key source, default
`name`; may be a LIST of fields joined into one slug for composite keys —
e.g. `["type", "name"]` where names are only unique within a type),
`renames` (post-snake-case API→schema names), `drops` (fields
always removed), `references` (force `{id,...}` unwrapping),
`drop_if_default` (remove a field when it equals the given value —
perma-diff suppression), `divide` (field→integer divisor: unit conversion
for fields where the provider schema stores a larger unit than the API
returns and converts internally — e.g. ZIA `size_quota` is KB on the API
but MB in config, so `"divide": {"size_quota": 1024}`; integer division,
applied before `drop_if_default` so a converted 0 still drops),
`ranges` (field→[min, max]: provider RUNTIME validator bounds mined from
provider source — invisible in the schema dump; enforced by `make lint`
so hand-edited values fail the PR gate instead of the plan stage, e.g.
`"ranges": {"size_quota": [10, 100000]}` — size_quota is MB in config),
`split_csv` (list of post-rename fields whose
comma-joined string values become real lists, empties dropped — ZCC
returns list-typed settings this way), `import_id` (format template over
the item's snake_cased original fields, default `"{id}"`), `acknowledged_drops`
(list of dotted drop paths that are expected/known-unmanageable — suppressed
from the drop report so only new provider-coverage surprises surface; the
fields are still removed from the generated tfvars), `skip_if` (list of
matchers; each matcher is a dict of field→value; an item is excluded
entirely when any matcher's pairs all match the snake_cased raw item —
use this for system/predefined objects the provider refuses to manage, e.g.
`"skip_if": [{"default_rule": true}]` drops any item where `default_rule`
is `true`). Exceptions are data, not code: prefer an entry here over
editing the transform.

The same JSON file may also carry one GENERATOR key: `sample` (a dict
merged over the generated module test fixture's example item) — for
required attributes with provider-validated enums where the default
`"example"` value cannot pass a mock plan, e.g.
`"sample": {"protocols": ["ANY_RULE"]}`.
