# Generator overrides

If `tools/overrides/<resource_type>/main.tf` exists, `make generate` uses
it verbatim instead of the rendered `main.tf` for that resource — the
escape hatch for provider quirks the generator cannot express. Each
override is a carried bug: record why in a comment at the top of the
file, and delete the override (then regenerate) when upstream fixes land.

## Transform override maps

`tools/overrides/<resource_type>.json` configures the transform for that
resource (all keys optional): `key_field` (map key source, default
`name`), `renames` (post-snake-case API→schema names), `drops` (fields
always removed), `references` (force `{id,...}` unwrapping),
`drop_if_default` (remove a field when it equals the given value —
perma-diff suppression), `import_id` (format template over the item's
snake_cased original fields, default `"{id}"`), `acknowledged_drops`
(list of dotted drop paths that are expected/known-unmanageable — suppressed
from the drop report so only new provider-coverage surprises surface; the
fields are still removed from the generated tfvars). Exceptions are data,
not code: prefer an entry here over editing the transform.
