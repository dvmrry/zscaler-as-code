"""Fill ONE config-carried field into STATE — zero tenant writes.

For the class of provider read bugs where a field the provider REQUIRES
in config can never be returned by its own read (the ISOLATE-rule
cbi_profile case: the detail GET omits it, the provider's
preserve-from-state workaround has nothing to preserve on a fresh
import, and the resulting plan is an update — a write — that
zero-change shops cannot approve). The fix is a STATE correction: copy
the committed config value (which came from the tenant's own pull) into
the state instance, so the plan converges with no API call. The
provider's preserve branch then keeps it on every future read.

Safety doctrine — this tool edits state, the most dangerous file in the
system, so it is deliberately narrow:
- the VALUE comes from committed config only (never free-form input),
  and config MUST be freshly fetched+transformed first — filling from a
  stale pull would hide real drift behind a converged-looking plan
- it only fills a field that is ABSENT/empty in state (never overwrites)
- the shape is built from the provider schema (every inner attribute
  present, config value or null, numbers coerced)
- serial is bumped, lineage untouched; everything else byte-identical
- file-in/file-out: `terraform state pull` feeds it, `terraform state
  push` ships it — both via make statefill, gated on STATE_FILL=1

Stdlib-only, Python 3.6-floor — see AGENTS.md rule 5.
"""
import json
import os
import sys

from tools import deployment
from tools.tfschema import load_resource

_EMPTY = (None, [], {}, "")


def _coerce(value, type_enc):
    if type_enc == "number" and isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            try:
                return float(value)
            except ValueError:
                return value
    return value


def build_state_value(field_schema, config_value):
    """State-shaped value for a block/attribute from its config value:
    every schema inner attribute present (config value or null) so the
    pushed state decodes cleanly against the provider schema."""
    if "block" in field_schema:  # nested block type
        inner = field_schema["block"].get("attributes") or {}
        elements = config_value if isinstance(config_value, list) else [config_value]
        out = []
        for element in elements:
            if not isinstance(element, dict):
                raise ValueError("config value elements must be objects")
            unknown = sorted(set(element) - set(inner))
            if unknown:
                raise ValueError(
                    "config carries keys the schema lacks: %s" % ", ".join(unknown))
            out.append(dict(
                (name, _coerce(element.get(name), (inner[name].get("type"))))
                for name in inner))
        return out
    return _coerce(config_value, field_schema.get("type"))


def fill_state(state_doc, resource_type, key, field, config_items):
    """-> (modified state_doc copy, summary lines). Raises ValueError
    with an actionable message on every guard violation."""
    rs = load_resource(resource_type)["block"]
    field_schema = (rs.get("block_types") or {}).get(field) or (
        rs.get("attributes") or {}).get(field)
    if field_schema is None:
        raise ValueError("%r is not a field of %s in the provider schema"
                         % (field, resource_type))
    if isinstance(field_schema, dict) and "block" not in field_schema \
            and field_schema.get("computed") and not field_schema.get("optional"):
        raise ValueError("%r is computed-only — the provider owns it; "
                         "filling it would be invention, not correction" % field)

    item = (config_items or {}).get(key)
    if item is None:
        raise ValueError("config carries no item %r — the fill value must "
                         "come from committed config (re-run make transform "
                         "first)" % key)
    if field not in item or item[field] in _EMPTY:
        raise ValueError("config item %r does not carry %r — nothing "
                         "verified to fill from" % (key, field))

    doc = json.loads(json.dumps(state_doc))  # deep copy
    module_addr = "module.%s" % resource_type
    instance = None
    for res in doc.get("resources") or []:
        if (res.get("module") == module_addr and res.get("mode") == "managed"
                and res.get("type") == resource_type
                and res.get("name") == "this"):
            for inst in res.get("instances") or []:
                if inst.get("index_key") == key:
                    instance = inst
                    break
    if instance is None:
        raise ValueError(
            'no state instance %s.%s.this["%s"] — the item must be IMPORTED '
            "first (terraform import / the bootstrap pipeline), then filled"
            % (module_addr, resource_type, key))

    attrs = instance.get("attributes") or {}
    current = attrs.get(field)
    if current not in _EMPTY:
        raise ValueError(
            "state already carries %s.%s = %r — refusing to overwrite a "
            "non-empty value (this tool only FILLS provider-read gaps)"
            % (key, field, current))

    new_value = build_state_value(field_schema, item[field])
    attrs[field] = new_value
    instance["attributes"] = attrs
    old_serial = doc.get("serial", 0)
    doc["serial"] = old_serial + 1
    summary = [
        "filled %s items[%s].%s from committed config:" % (resource_type, key, field),
        "  %s" % json.dumps(new_value, sort_keys=True),
        "serial %d -> %d (lineage untouched)" % (old_serial, doc["serial"]),
    ]
    return doc, summary


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 5:
        sys.stderr.write(
            "usage: python -m tools.statefill <state.json> <resource_type> "
            "<key> <field> <tenant>   (make statefill)\n")
        return 2
    state_path, resource_type, key, field, tenant = argv
    config_path = os.path.join(
        deployment.config_dir(tenant), resource_type + ".auto.tfvars.json")
    try:
        with open(state_path, encoding="utf-8") as f:
            state_doc = json.load(f)
        with open(config_path, encoding="utf-8") as f:
            config_items = json.load(f).get("items") or {}
        doc, summary = fill_state(
            state_doc, resource_type, key, field, config_items)
    except (ValueError, OSError) as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 1
    json.dump(doc, sys.stdout, indent=2, sort_keys=False)
    sys.stdout.write("\n")
    for line in summary:
        sys.stderr.write(line + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
