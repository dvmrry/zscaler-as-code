# Generator overrides

If `tools/overrides/<resource_type>/main.tf` exists, `make generate` uses
it verbatim instead of the rendered `main.tf` for that resource — the
escape hatch for provider quirks the generator cannot express. Each
override is a carried bug: record why in a comment at the top of the
file, and delete the override (then regenerate) when upstream fixes land.
