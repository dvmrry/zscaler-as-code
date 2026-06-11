# Generator overrides

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
perma-diff suppression), `split_csv` (list of post-rename fields whose
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
