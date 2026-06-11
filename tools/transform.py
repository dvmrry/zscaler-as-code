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

from tools.tfschema import block_is_single, classify_attributes, load_resource

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
    drops (the provider-coverage-gap report). Block handling branches on
    block_is_single: single-instance blocks (nesting_mode "single" or
    max_items=1) carry one dict (kept as a bare object, NOT wrapped in a
    list — the generator wraps [x] at plan time); multi-instance blocks
    carry a list of dicts.
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
            if block_is_single(block_types[key]):
                # single-instance block: value is ONE object. Tolerate list
                # shapes from the API: unwrap [x]; MERGE longer lists the
                # way the provider's own flattener does (ZIA ID-groups
                # return N {id, name} elements for a max_items=1 block
                # whose real members are lists).
                single = value
                if isinstance(single, list):
                    if not single:
                        # empty list = "none set" — omit silently; this is
                        # absence of data, not a provider coverage gap.
                        continue
                    elems = [v for v in single if isinstance(v, dict)]
                    if not elems:
                        drops.append(child_path)
                        continue
                    if len(elems) == 1:
                        single = elems[0]
                    else:
                        single = _merge_block_elements(
                            elems, inner_block, child_path, drops
                        )
                if isinstance(single, dict):
                    if _is_null_object(single):
                        # provider-mirror: the "not configured" stub is
                        # absence of data, omitted silently.
                        continue
                    out[key] = filter_item(single, inner_block, child_path, drops)
                else:
                    drops.append(child_path)
            else:
                inner_path = child_path + "[]"
                if isinstance(value, list):
                    out[key] = [
                        filter_item(v, inner_block, inner_path, drops)
                        for v in value
                        if isinstance(v, dict) and not _is_null_object(v)
                    ]
                elif isinstance(value, dict):
                    if _is_null_object(value):
                        out[key] = []
                    else:
                        out[key] = [
                            filter_item(value, inner_block, inner_path, drops)
                        ]
                else:
                    drops.append(child_path)
        else:
            drops.append(child_path)
    return out


_NULL_STUB_VALUES = (0, "0", "", None)


def _is_null_object(obj):
    """True for the ZIA/ZPA "not configured" block stub.

    The APIs emit id-bearing stubs for unset block fields — extranet
    {"id": 0}, cbi_profile {"id": "0", "name": "", ...}, ID-collection
    elements {"id": 0} — and the providers' OWN flatteners treat them as
    absent (flattenCustomIDSet: `if customID == nil || customID.ID == 0
    { return nil }`, v4.7.24). Config must mirror that or every adoption
    plan shows perpetual phantom diffs on these blocks.

    Conservative shape: an id-ish key ('id', or every key ending in
    'id') whose value is 0/"0"/""/None/[], and every other member also
    zero-ish. Any boolean member (even False) marks the block as real
    settings, never a stub.
    """
    if not isinstance(obj, dict) or not obj:
        return False
    keys = list(obj)
    if "id" not in obj and not all(k.endswith("id") for k in keys):
        return False
    for value in obj.values():
        if isinstance(value, bool):
            return False
        if value in _NULL_STUB_VALUES or value == []:
            continue
        return False
    return True


def _merge_block_elements(elems, block, path, drops):
    """Collapse N raw elements of a single-instance block into one dict,
    mirroring the provider's own flattener: list/set-typed members union
    across elements (scalars wrap; empty strings mean empty and are
    skipped), every other key keeps its first value. A later conflicting
    value for a schema input is recorded in drops — never silently lost.
    """
    cls = classify_attributes(block)
    inputs = set(cls["required"] + cls["optional"])
    attrs = block.get("attributes") or {}
    merged = {}
    for elem in elems:
        for k in sorted(elem):
            v = elem[k]
            if v is None:
                continue
            enc = attrs.get(k, {}).get("type")
            if isinstance(enc, list) and len(enc) == 2 and enc[0] in ("list", "set"):
                bucket = merged.setdefault(k, [])
                if v == "":
                    continue
                bucket.extend(v if isinstance(v, list) else [v])
            elif k not in merged:
                merged[k] = v
            elif merged[k] != v and k in inputs:
                drops.append(
                    "%s.%s (conflicting values across merged elements; "
                    "kept first)" % (path, k)
                )
    return merged


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
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            if value.lower() in ("true", "1"):
                return True
            if value.lower() in ("false", "0"):
                return False
        if isinstance(value, int):
            # Mirror the provider's own helper (zcc IntToBool): any
            # non-zero integer is true — ZCC uses tri-state ints (e.g. 2)
            # for some flags and the provider reads them all as i != 0.
            return value != 0
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
    Block values recurse with their inner schema, branching on
    block_is_single: single-instance blocks are a single dict (recurse into
    it directly); multi-instance blocks are lists of dicts (filter_item ran
    first).
    """
    attrs = block.get("attributes") or {}
    block_types = block.get("block_types") or {}
    out = {}
    for key in sorted(item):
        value = item[key]
        if key in block_types:
            inner = block_types[key]["block"]
            if block_is_single(block_types[key]):
                out[key] = coerce_item(value, inner) if isinstance(value, dict) else value
            else:
                out[key] = [coerce_item(v, inner) for v in value] if isinstance(value, list) else value
            continue
        enc = attrs.get(key, {}).get("type")
        if isinstance(enc, str):
            out[key] = _coerce_primitive(_unwrap_ref(value), enc)
        elif isinstance(enc, list) and len(enc) == 2 and isinstance(enc[1], str):
            if value == "":
                out[key] = []
            elif isinstance(value, list):
                out[key] = [
                    _coerce_primitive(_unwrap_ref(v), enc[1]) for v in value
                ]
            elif value is None:
                out[key] = value
            else:
                out[key] = [_coerce_primitive(_unwrap_ref(value), enc[1])]
        elif (
            isinstance(enc, list) and len(enc) == 2
            and isinstance(enc[1], list) and len(enc[1]) == 2 and enc[1][0] == "object"
        ):
            # Object-typed list/set ATTRIBUTE (e.g. tcp_port_range:
            # ["list", ["object", {"from": "string", "to": "string"}]]).
            # Structurally identical to a list block from the API's view, so
            # coerce each member by its declared type — same quirk-6 / ref
            # unwrap treatment block members get (filter_item leaves these
            # attribute values untouched, so coerce handles them here).
            members = enc[1][1]
            out[key] = [
                _coerce_object_members(v, members) if isinstance(v, dict) else v
                for v in value
            ] if isinstance(value, list) else value
        else:
            out[key] = value
    return out


def _coerce_object_members(obj, members):
    """Coerce each member of an object-typed-list element by its declared
    primitive type, unwrapping {id,...} reference objects first.

    Keys absent from the declared members are DROPPED, not passed through:
    the generated HCL type is a strict object({...}), so an undeclared key
    fails `terraform plan`. filter_item strips API-extra keys from block
    values; structurally-identical object-list attribute values get the same
    treatment here (filter_item leaves attribute values untouched). Declared
    members with non-primitive encodings (none exist in the current dumps)
    pass through uncoerced rather than being silently lost."""
    out = {}
    for k in sorted(obj):
        enc = members.get(k)
        if isinstance(enc, str):
            out[k] = _coerce_primitive(_unwrap_ref(obj[k]), enc)
        elif enc is not None:
            out[k] = obj[k]
    return out


OVERRIDES_DIR = os.path.join("tools", "overrides")


def load_override(resource_type):
    path = os.path.join(OVERRIDES_DIR, resource_type + ".json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    # Validate authoring-side once at load (not per item): a 0 divisor would
    # raise a bare ZeroDivisionError deep in apply_overrides with no clue
    # which override file is wrong. Name the field and the file instead.
    for field, divisor in (data.get("divide") or {}).items():
        if divisor == 0:
            raise ValueError(
                "divide divisor for %r in %s must be non-zero" % (field, path)
            )
    return data


def apply_overrides(item, override):
    """Renames, CSV splitting, unconditional drops, forced reference
    unwrapping, drop-if-default. Post-snake, pre-filter, so renamed fields
    are filtered under their schema names."""
    out = dict(item)
    for old, new in sorted((override.get("renames") or {}).items()):
        if old in out:
            out[new] = out.pop(old)
    for field in sorted(override.get("split_csv") or []):
        # Some APIs (ZCC) return list-typed settings as comma-joined
        # strings; split into real lists, dropping empties.
        if field in out and isinstance(out[field], str):
            out[field] = [v.strip() for v in out[field].split(",") if v.strip()]
    for field in sorted(override.get("drops") or []):
        out.pop(field, None)
    for field in sorted(override.get("references") or {}):
        if field in out:
            value = out[field]
            if isinstance(value, list):
                out[field] = [_unwrap_ref(v) for v in value]
            else:
                out[field] = _unwrap_ref(value)
    for field, divisor in sorted((override.get("divide") or {}).items()):
        # Unit conversion: some provider schemas store a field in a larger
        # unit than the API returns and convert internally (e.g. ZIA
        # size_quota — API speaks KB, the schema value is MB, and the
        # provider does `resp.SizeQuota / 1024` on read). Mirror that
        # integer division so config matches what the provider would
        # store. Runs before drop_if_default so a divided 0 still drops.
        if field in out:
            value = out[field]
            if isinstance(value, str):
                try:
                    value = int(value)
                except ValueError:
                    continue
            if isinstance(value, bool) or not isinstance(value, int):
                continue
            out[field] = value // divisor
    for field, default in sorted((override.get("drop_if_default") or {}).items()):
        # Compare against the default after the same string-int coercion the
        # divide step does, so an API number-as-string (quirk 5) like
        # time_quota:'0' still matches an int default 0 even when the field
        # is not divided. bool is an int subclass, so guard it out.
        if field not in out:
            continue
        val = out[field]
        if (isinstance(default, int) and not isinstance(default, bool)
                and isinstance(val, str)):
            try:
                val = int(val)
            except ValueError:
                pass
        if val == default:
            del out[field]
    return out


def _skip_item(snake_raw, override):
    """True when any skip_if matcher fully matches the snake_cased raw
    item. skip_if is the item-level exclusion for unmanageable system
    objects (e.g. predefined default rules the provider refuses)."""
    for matcher in override.get("skip_if") or []:
        if all(snake_raw.get(f) == v for f, v in matcher.items()):
            return True
    return False


def derive_key(item, override):
    """Stable map key from the override's key_field — a single field name
    or a LIST of fields joined into one slug (composite keys, for
    resources whose names are only unique within a type, e.g. cloud app
    control rules across rule types)."""
    field = override.get("key_field", "name")
    fields = field if isinstance(field, list) else [field]
    parts = []
    for f in fields:
        if f not in item:
            raise KeyError(
                "key field %r missing from item; set key_field in the "
                "override map" % f
            )
        parts.append(str(item[f]))
    slug = slugify(" ".join(parts))
    if slug == "":
        # The name(s) had no ASCII-alphanumerics (e.g. CJK or other
        # non-Latin scripts), so slugify stripped everything. Fall back to a
        # stable, unique, human-recognizable key derived from the id so two
        # distinct non-ASCII-named items never collide on '' and no
        # this[""] address is ever emitted.
        ident = item.get("id")
        if ident is None:
            raise ValueError(
                "derived key is empty for %s (name(s) %r have no ASCII "
                "letters/digits) and item has no 'id' to fall back on; set "
                "key_field in the override map" % (fields, parts)
            )
        slug = "id_%s" % slugify(str(ident))
    return slug


def transform_items(raw_items, resource_type, override):
    """Full per-item pipeline. Returns (items_map, originals_map, drops).

    Stage order matters: filter_item runs first so coerce_item sees block
    values already shaped by nesting_mode (single -> dict, list/set -> list
    of dicts).
    """
    rs = load_resource(resource_type)
    block = rs["block"]
    items = {}
    originals = {}
    drops = []
    for raw in raw_items:
        snake_raw = snake_keys(raw)
        if _skip_item(snake_raw, override):
            sys.stderr.write(
                "skipped %s item %r (skip_if matched)\n"
                % (resource_type, snake_raw.get("name") or snake_raw.get("id"))
            )
            continue
        normalized = apply_overrides(snake_raw, override)
        key = derive_key(normalized, override)
        if key in items:
            raise ValueError(
                "duplicate derived key %r for %s; set a different key_field "
                "in the override map" % (key, resource_type)
            )
        filtered = filter_item(normalized, block, "", drops)
        items[key] = coerce_item(filtered, block)
        originals[key] = normalized
    acknowledged = set(override.get("acknowledged_drops") or [])
    reported = sorted(d for d in set(drops) if d not in acknowledged)
    return items, originals, reported


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


_IMPORT_PAIR_RE = re.compile(
    r'to = module\.[\w]+\.[\w]+\.this\["(.+?)"\]\s*\n\s*id = "(.+?)"'
)


def parse_import_pairs(imports_text):
    """{key: import_id} from a rendered imports file."""
    return dict(_IMPORT_PAIR_RE.findall(imports_text))


def derive_moves(old_imports_text, new_imports_text):
    """Detect console renames: same import id under a different config key.

    A rename in the console changes the derived map key, which terraform
    sees as destroy-old-address + create-new-address — a destroy/create
    of a LIVE object. The import id is identity (unique per resource), so
    same-id-different-key pairs become `moved` blocks instead, making the
    rename a pure state-address change. Returns sorted (old_key, new_key)
    pairs.
    """
    old_pairs = parse_import_pairs(old_imports_text)
    new_pairs = parse_import_pairs(new_imports_text)
    old_by_id = {}
    for key, import_id in old_pairs.items():
        old_by_id.setdefault(import_id, key)
    moves = []
    for new_key, import_id in new_pairs.items():
        old_key = old_by_id.get(import_id)
        if old_key is not None and old_key != new_key and old_key not in new_pairs:
            moves.append((old_key, new_key))
    return sorted(moves)


def render_moves(resource_type, moves):
    blocks = []
    for old_key, new_key in moves:
        blocks.append(
            "moved {\n"
            '  from = module.%s.%s.this["%s"]\n'
            '  to   = module.%s.%s.this["%s"]\n'
            "}\n" % (
                resource_type, resource_type, old_key,
                resource_type, resource_type, new_key,
            )
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
    with open(input_path, encoding="utf-8") as f:
        raw_items = json.load(f)
    _warn_if_slim(raw_items, load_resource(resource_type)["block"], resource_type)
    items, originals, drops = transform_items(raw_items, resource_type, override)
    config_dir = os.path.join("config", tenant)
    imports_dir = os.path.join("imports", tenant)
    os.makedirs(config_dir, exist_ok=True)
    os.makedirs(imports_dir, exist_ok=True)
    tfvars_path = os.path.join(config_dir, resource_type + ".auto.tfvars.json")
    imports_path = os.path.join(imports_dir, resource_type + "_imports.tf")
    moves_path = os.path.join(imports_dir, resource_type + "_moves.tf")
    new_imports = render_imports(resource_type, originals, override)
    # Console renames: compare the previously committed imports (key->id)
    # with the fresh ones; same id under a new key becomes a moved block so
    # the rename is a state-address change, not destroy+create of a live
    # object. The moves file is staged ONLY when renames exist; copy it
    # into the env root alongside the imports file and delete after apply.
    if os.path.exists(imports_path):
        with open(imports_path, encoding="utf-8") as f:
            moves = derive_moves(f.read(), new_imports)
        if moves:
            with open(moves_path, "w", encoding="utf-8") as f:
                f.write(render_moves(resource_type, moves))
            sys.stderr.write(
                "RENAME(S) DETECTED: %d item(s) re-keyed — moved blocks "
                "staged in %s; copy into the env root alongside the imports "
                "file before plan/apply (RUNBOOK: Drift)\n"
                % (len(moves), moves_path)
            )
    with open(tfvars_path, "w", encoding="utf-8") as f:
        f.write(render_tfvars(items))
    with open(imports_path, "w", encoding="utf-8") as f:
        f.write(new_imports)
    # drops contains only unacknowledged paths; acknowledged_drops in the override
    # suppress known-unmanageable metadata from this report (fields still removed).
    for path in drops:
        sys.stderr.write("dropped %s.%s\n" % (resource_type, path))
    sys.stderr.write("wrote %s\nwrote %s\n" % (tfvars_path, imports_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
