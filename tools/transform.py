"""Transform detail-shaped Zscaler API JSON into tfvars + import blocks.

The one component that must run in restricted environments: stdlib-only,
Python 3.6-floor, file in -> files out, no network, no credentials. Driven
by the committed provider schemas (tools/tfschema.py) plus per-resource
override maps (tools/overrides/<type>.json) — exceptions are data, not
code. See AGENTS.md rules 5, 7, 8.
"""
import json
import os
import re
import sys

from tools.tfschema import classify_attributes, load_resource

_SNAKE_1 = re.compile(r"(.)([A-Z][a-z]+)")
_SNAKE_2 = re.compile(r"([a-z0-9])([A-Z])")
_SLUG_BAD = re.compile(r"[^a-z0-9]+")


def snake(name):
    half = _SNAKE_1.sub(r"\1_\2", name)
    return _SNAKE_2.sub(r"\1_\2", half).lower()


def snake_keys(value):
    """Recursively snake_case every dict key."""
    if isinstance(value, dict):
        return dict((snake(k), snake_keys(v)) for k, v in value.items())
    if isinstance(value, list):
        return [snake_keys(v) for v in value]
    return value


def slugify(text):
    """Stable map key from a display name: lowercase, runs of other
    characters become single underscores, edges stripped."""
    return _SLUG_BAD.sub("_", text.lower()).strip("_")


def filter_item(item, block, path, drops):
    """Keep only schema-input attrs and blocks, recursively.

    Computed-only and unknown keys are dropped and their paths recorded in
    drops (the provider-coverage-gap report). Block values may be a list
    of dicts (list/set nesting) or a single dict (single nesting).
    """
    cls = classify_attributes(block)
    keep_attrs = set(cls["required"] + cls["optional"])
    block_types = block.get("block_types") or {}
    out = {}
    for key in sorted(item):
        child_path = path + key if not path else path + "." + key
        value = item[key]
        if key in keep_attrs:
            out[key] = value
        elif key in block_types:
            inner_block = block_types[key]["block"]
            inner_path = child_path + "[]"
            if isinstance(value, list):
                out[key] = [
                    filter_item(v, inner_block, inner_path, drops)
                    for v in value
                    if isinstance(v, dict)
                ]
            elif isinstance(value, dict):
                out[key] = [filter_item(value, inner_block, inner_path, drops)]
            else:
                drops.append(child_path)
        else:
            drops.append(child_path)
    return out


def _coerce_primitive(value, prim):
    if prim == "string":
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return ("%d" % value) if isinstance(value, int) else repr(value)
        return value
    if prim == "number":
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                try:
                    return float(value)
                except ValueError:
                    return value
        return value
    if prim == "bool":
        if isinstance(value, str):
            if value.lower() in ("true", "1"):
                return True
            if value.lower() in ("false", "0"):
                return False
        return value  # "yes"/"no" style strings pass through; terraform reports the type error
    return value


def _unwrap_ref(value):
    if isinstance(value, dict) and "id" in value:
        return value["id"]
    return value


def coerce_item(item, block):
    """Schema-driven coercion + mechanical {id,...} reference unwrapping.

    When the schema expects a primitive (or collection of primitives) and
    the API handed us reference objects, unwrap to ids before coercing.
    Block values recurse with their inner schema.
    Expects block values already normalised to lists by filter_item.
    """
    attrs = block.get("attributes") or {}
    block_types = block.get("block_types") or {}
    out = {}
    for key in sorted(item):
        value = item[key]
        if key in block_types:
            inner = block_types[key]["block"]
            out[key] = [coerce_item(v, inner) for v in value] if isinstance(value, list) else value
            continue
        enc = attrs.get(key, {}).get("type")
        if isinstance(enc, str):
            out[key] = _coerce_primitive(_unwrap_ref(value), enc)
        elif isinstance(enc, list) and len(enc) == 2 and isinstance(enc[1], str):
            if isinstance(value, list):
                out[key] = [
                    _coerce_primitive(_unwrap_ref(v), enc[1]) for v in value
                ]
            else:
                out[key] = value
        else:
            out[key] = value
    return out


OVERRIDES_DIR = os.path.join("tools", "overrides")


def load_override(resource_type):
    path = os.path.join(OVERRIDES_DIR, resource_type + ".json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def apply_overrides(item, override):
    """Renames, forced reference unwrapping, drop-if-default. Post-snake,
    pre-filter, so renamed fields are filtered under their schema names."""
    out = dict(item)
    for old, new in sorted((override.get("renames") or {}).items()):
        if old in out:
            out[new] = out.pop(old)
    for field in sorted(override.get("references") or {}):
        if field in out:
            value = out[field]
            if isinstance(value, list):
                out[field] = [_unwrap_ref(v) for v in value]
            else:
                out[field] = _unwrap_ref(value)
    for field, default in sorted((override.get("drop_if_default") or {}).items()):
        if field in out and out[field] == default:
            del out[field]
    return out


def derive_key(item, override):
    field = override.get("key_field", "name")
    if field not in item:
        raise KeyError(
            "key field %r missing from item; set key_field in the override map" % field
        )
    return slugify(str(item[field]))


def transform_items(raw_items, resource_type, override):
    """Full per-item pipeline. Returns (items_map, originals_map, drops).

    Stage order matters: filter_item runs before coerce_item because
    coerce_item expects block values already normalised to lists.
    """
    rs = load_resource(resource_type)
    block = rs["block"]
    items = {}
    originals = {}
    drops = []
    for raw in raw_items:
        normalized = apply_overrides(snake_keys(raw), override)
        key = derive_key(normalized, override)
        if key in items:
            raise ValueError(
                "duplicate derived key %r for %s; set a different key_field "
                "in the override map" % (key, resource_type)
            )
        filtered = filter_item(normalized, block, "", drops)
        items[key] = coerce_item(filtered, block)
        originals[key] = normalized
    return items, originals, sorted(set(drops))


def render_tfvars(items):
    return json.dumps({"items": items}, indent=2, sort_keys=True) + "\n"


def render_imports(resource_type, originals, override):
    template = override.get("import_id", "{id}")
    blocks = []
    for key in sorted(originals):
        import_id = template.format(**originals[key])
        blocks.append(
            "import {\n"
            '  to = module.%s.%s.this["%s"]\n'
            '  id = "%s"\n'
            "}\n" % (resource_type, resource_type, key, import_id)
        )
    return "\n".join(blocks)


def _warn_if_slim(raw_items, block, resource_type):
    cls = classify_attributes(block)
    expected = len(cls["required"]) + len(cls["optional"])
    if not raw_items or expected == 0:
        return
    avg = sum(len(i) for i in raw_items) / float(len(raw_items))
    if avg < expected / 3.0:
        sys.stderr.write(
            "WARNING: %s input looks slim (avg %.1f keys vs %d schema inputs); "
            "did the fetcher use the list endpoint instead of detail?\n"
            % (resource_type, avg, expected)
        )


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 3:
        sys.stderr.write(
            "usage: python -m tools.transform <resource_type> <input.json> <tenant>\n"
        )
        return 2
    resource_type, input_path, tenant = argv
    override = load_override(resource_type)
    with open(input_path) as f:
        raw_items = json.load(f)
    _warn_if_slim(raw_items, load_resource(resource_type)["block"], resource_type)
    items, originals, drops = transform_items(raw_items, resource_type, override)
    config_dir = os.path.join("config", tenant)
    imports_dir = os.path.join("imports", tenant)
    os.makedirs(config_dir, exist_ok=True)
    os.makedirs(imports_dir, exist_ok=True)
    tfvars_path = os.path.join(config_dir, resource_type + ".auto.tfvars.json")
    imports_path = os.path.join(imports_dir, resource_type + "_imports.tf")
    with open(tfvars_path, "w") as f:
        f.write(render_tfvars(items))
    with open(imports_path, "w") as f:
        f.write(render_imports(resource_type, originals, override))
    for path in drops:
        sys.stderr.write("dropped %s.%s\n" % (resource_type, path))
    sys.stderr.write("wrote %s\nwrote %s\n" % (tfvars_path, imports_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
