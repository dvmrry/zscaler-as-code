"""Schema-driven adversarial conformance harness.

For every resource type in the registry, synthesize raw API-style items that
deliberately exercise the full quirk catalog (camelCase keys, bool-as-int /
bool-as-string, numbers-as-strings, CSV-joined lists, {id,name} reference
objects, single-dict-for-list blocks, single-mode object blocks,
multi-element lists for max_items=1 blocks — the ZIA ID-group pattern the
transform merges into one object — etc.), push them through the REAL
transform pipeline with the REAL override map, then verify the output
structurally against the provider schema with the REAL typecheck. Zero
mismatches is the contract.

This makes future modules deterministic: a new registry entry is automatically
synthesized and conformance-tested against every quirk before any tenant
contact, with no hand-written fixture. Synthesis is fully deterministic — same
seed yields byte-identical output (no RNG; variation is a stable function of
the field name and a per-walk counter).

Stdlib-only, Python 3.6-floor — no f-strings, no walrus, no match. See
AGENTS.md rule 5.
"""
import json
import sys

from tools.registry import generated_types
from tools.tfschema import block_is_single, classify_attributes, load_resource
from tools.transform import snake


# ---------------------------------------------------------------------------
# camelCase inversion (snake -> a camelCase form that snake() maps back)
# ---------------------------------------------------------------------------

def camel(name):
    """Invert snake_case into a camelCase API-style key.

    Mechanical inverse of transform.snake for synthesis: the result, fed back
    through snake(), MUST reproduce the input — that round-trip is the only
    property the pipeline relies on (it snake_cases keys before anything
    else). snake() is many-to-one (acronym casing is lossy), so this returns
    one valid pre-image, not the literal API spelling.

    Built boundary by boundary: capitalizing the next segment's initial only
    merges the underscore when snake() would split it back. At a digit
    boundary (e.g. dns_64prefix) capitalizing does nothing and snake() leaves
    no separator, so the underscore is retained — snake() preserves existing
    underscores, keeping the round-trip exact.
    """
    parts = name.split("_")
    out = parts[0]
    consumed = parts[0]  # snake-form of what `out` should map back to so far
    for part in parts[1:]:
        merged = out + part[:1].upper() + part[1:]
        target = consumed + "_" + part
        if snake(merged) == target:
            out = merged  # capitalizing round-trips: merge the boundary
        else:
            out = out + "_" + part  # digit boundary etc: keep the underscore
        consumed = target
    # Guard: a snake_case key snakes to itself, so this fallback is always a
    # valid pre-image even if the per-boundary logic ever missed a case.
    if snake(out) != name:
        return name
    return out


# ---------------------------------------------------------------------------
# Deterministic variation
# ---------------------------------------------------------------------------

def _variant(seed, counter, field, modulo):
    """Stable, reproducible variation index in [0, modulo).

    A pure function of the synthesis seed, a per-walk counter, and the field
    name. No randomness: re-running with the same seed yields the same index,
    so synthesize_item is byte-for-byte reproducible. The field-name term
    spreads quirk coverage across sibling fields within one item.
    """
    h = 0
    for ch in field:
        h = (h * 131 + ord(ch)) & 0x7FFFFFFF
    return (seed * 7 + counter * 3 + h) % modulo


class _Counter(object):
    """Monotonic per-walk counter so each emitted primitive gets a distinct
    variation even when two sibling fields share a name across nesting."""

    def __init__(self):
        self.n = 0

    def next(self):
        v = self.n
        self.n += 1
        return v


# ---------------------------------------------------------------------------
# Per-primitive synthesis (adversarial: emit the quirky raw shape)
# ---------------------------------------------------------------------------

def _syn_string(seed, ctr, field):
    """Strings: plain, or a number/bool the string-coercion must stringify
    (quirk 6). Never empty for key/name fields (handled by the caller)."""
    v = _variant(seed, ctr.next(), field, 4)
    if v == 0:
        return "%s_value" % field
    if v == 1:
        return 7  # int where schema wants string -> "7"
    if v == 2:
        return True  # bool where schema wants string -> "true"
    return "%s-%d" % (field, seed)


def _syn_number(seed, ctr, field):
    """Numbers: int, numeric string (quirk 5), or {id,name} ref object whose
    id is itself a numeric string (quirks 5 + 9 combined)."""
    v = _variant(seed, ctr.next(), field, 3)
    if v == 0:
        return 443
    if v == 1:
        return "443"  # number-as-string
    return {"id": "9", "name": "%s_ref" % field}  # ref object, id coerced


def _syn_bool(seed, ctr, field):
    """Bools, the richest quirk family: real bool, tri-state ints (0/1/2 all
    via the provider's i!=0 rule), and "0"/"1"/"false" strings (quirks 2,3).
    """
    v = _variant(seed, ctr.next(), field, 6)
    return [True, 1, 2, "1", "0", "false"][v]


def _syn_list_primitive(seed, ctr, field, prim, csv_fields):
    """list/set of primitives. Universal quirks 8/10/12 apply to every list
    field; CSV-joining (quirk 7) is per-resource, so only emit a comma-joined
    string when this field is in the override's split_csv (otherwise the raw
    string would survive to coercion as a plain string and fail typecheck).
    """
    if field in csv_fields:
        # split_csv field: emit the comma-joined string the ZCC APIs return,
        # plus the empty-string-as-empty-list case.
        v = _variant(seed, ctr.next(), field, 3)
        if v == 0:
            return "10.0.0.53, 10.0.1.53"  # CSV -> split, whitespace stripped
        if v == 1:
            return ""  # "" -> []
        return "10.0.0.0/24"  # single element, no comma
    v = _variant(seed, ctr.next(), field, 5)
    if prim == "number":
        if v == 0:
            return [443, 8080]
        if v == 1:
            return [{"id": 1, "name": "a"}, {"id": 2}]  # ref objects -> [1, 2]
        if v == 2:
            return ""  # -> []
        if v == 3:
            return 10  # scalar -> [10]
        return None  # null passes through untouched
    # primitive strings (and bool-of-string fall here too as list(string))
    if v == 0:
        return ["%s_a" % field, "%s_b" % field]
    if v == 1:
        return ""  # -> []
    if v == 2:
        return "%s_solo" % field  # scalar -> ["..._solo"]
    if v == 3:
        return None  # null
    return [{"id": "9", "name": "g"}]  # ref objects in a string list -> ["9"]


def _syn_object_list_attr(seed, ctr, field, members):
    """Object-typed list ATTRIBUTE (not a block) — e.g. tcp_port_range:
    [list,[object,{from,to}]]. Emit a list of member objects with camelCased
    keys so snake_keys re-normalizes them."""
    obj = {}
    for mk in sorted(members):
        obj[camel(mk)] = _syn_by_encoding(members[mk], seed, ctr, mk, set())
    return [obj]


def _syn_by_encoding(enc, seed, ctr, field, csv_fields):
    """Dispatch on a Terraform type encoding to a raw adversarial value."""
    if isinstance(enc, str):
        if enc == "string":
            return _syn_string(seed, ctr, field)
        if enc == "number":
            return _syn_number(seed, ctr, field)
        if enc == "bool":
            return _syn_bool(seed, ctr, field)
        return "%s_value" % field
    if isinstance(enc, list) and len(enc) == 2:
        kind, inner = enc
        if kind in ("list", "set") and isinstance(inner, str):
            return _syn_list_primitive(seed, ctr, field, inner, csv_fields)
        if kind in ("list", "set") and isinstance(inner, list) and inner and inner[0] == "object":
            return _syn_object_list_attr(seed, ctr, field, inner[1])
    return "%s_value" % field


# ---------------------------------------------------------------------------
# Block / item synthesis
# ---------------------------------------------------------------------------

def _syn_block_object(block, seed, ctr, csv_fields, rename_targets):
    """Synthesize one raw object for a block (or the top-level item body).

    Emits EVERY schema-input attribute and EVERY nested block with camelCase
    keys. rename_targets maps schema-name -> api-name for fields the override
    renames, so they are emitted under the pre-rename API name and the rename
    step moves them back. csv_fields is the override's split_csv set (only
    meaningful at the top level, where renames have landed).
    """
    cls = classify_attributes(block)
    attrs = block.get("attributes") or {}
    block_types = block.get("block_types") or {}
    out = {}

    for name in sorted(cls["required"] + cls["optional"]):
        enc = attrs[name].get("type")
        api_name = rename_targets.get(name, name)
        out[camel(api_name)] = _syn_by_encoding(enc, seed, ctr, name, csv_fields)

    for bname in sorted(block_types):
        bt = block_types[bname]
        inner = bt["block"]
        mode = bt["nesting_mode"]
        # Nested blocks never carry top-level renames/split_csv; pass empties.
        child = _syn_block_object(inner, seed, ctr, set(), {})
        if mode == "single":
            v = _variant(seed, ctr.next(), bname, 2)
            # single-mode: a bare dict, OR a one-element list to exercise the
            # legacy-list unwrap tolerance (quirk: single block from odd API).
            out[camel(bname)] = child if v == 0 else [child]
        elif block_is_single(bt):
            # max_items=1 list/set block: a bare dict, a one-element list,
            # the multi-element API shape (quirk 14 — the ZIA ID-group
            # pattern: N {id, ...} elements the transform must merge into
            # ONE object with unioned list members), OR the "not
            # configured" id-zero stub the transform must DROP (quirk 15;
            # mirrors the provider's flattenCustomIDSet ID==0 -> nil).
            stub = _null_stub(inner)
            v = _variant(seed, ctr.next(), bname, 4 if stub else 3)
            if v == 0:
                out[camel(bname)] = child
            elif v == 1:
                out[camel(bname)] = [child]
            elif v == 2:
                child2 = _syn_block_object(inner, seed + 1, ctr, set(), {})
                out[camel(bname)] = [child, child2]
            else:
                out[camel(bname)] = stub
        else:
            stub = _null_stub(inner)
            v = _variant(seed, ctr.next(), bname, 3 if stub else 2)
            # list/set block: a list of dicts, a single bare dict
            # (single-dict-for-list wrap, quirk 13), or a list carrying a
            # "not configured" stub element to drop (quirk 15).
            if v == 0:
                child2 = _syn_block_object(inner, seed + 1, ctr, set(), {})
                out[camel(bname)] = [child, child2]
            elif v == 1:
                out[camel(bname)] = child  # bare dict -> wrapped to [dict]
            else:
                out[camel(bname)] = [child, stub]
    return out


def _null_stub(block):
    """The id-zero "not configured" stub for a block, or None when the
    block has no id-ish member to stub (quirk 15 only exists for
    id-bearing blocks)."""
    attrs = block.get("attributes") or {}
    if "id" in attrs:
        return {camel("id"): 0}
    for name in sorted(attrs):
        if name.endswith("id"):
            return {camel(name): 0}
    return None


def _rename_targets(override):
    """schema-name -> api-name from the override's renames map.

    The override stores renames as {api_snake: schema_snake}; we invert it so
    the synthesizer can look up, for a schema field, the API name to emit.
    """
    out = {}
    for api_snake, schema_snake in (override.get("renames") or {}).items():
        out[schema_snake] = api_snake
    return out


def synthesize_item(resource_type, seed):
    """Build one raw API-style item for a resource, deterministically.

    Walks the provider schema's input attrs + blocks recursively and emits a
    RAW dict exercising the quirk catalog. Honors the override map: renamed
    fields emit under their API names; split_csv fields emit comma-joined
    strings; key_field fields (incl composite) are present and unique per
    seed; skip_if marker fields are present but set NOT to match; the import
    template's fields (always 'id') are present. Same seed -> identical bytes.
    """
    from tools.transform import load_override

    override = load_override(resource_type)
    block = load_resource(resource_type)["block"]
    csv_fields = set(override.get("split_csv") or [])
    rename_targets = _rename_targets(override)

    item = _syn_block_object(block, seed, _Counter(), csv_fields, rename_targets)

    # Inject non-schema fields the override pipeline needs, under camelCase
    # API names so snake_keys normalizes them. Unique per seed.
    item["id"] = "%d" % (1000 + seed)

    # key_field(s): guarantee presence and per-seed uniqueness so 3 synthesized
    # items never collide on the derived map key.
    key_field = override.get("key_field", "name")
    key_fields = key_field if isinstance(key_field, list) else [key_field]
    for kf in key_fields:
        api_kf = camel(rename_targets.get(kf, kf))
        item[api_kf] = "%s_%d" % (kf, seed)

    # skip_if markers: present but set to a NON-matching value so the item is
    # NOT skipped (we want it to flow all the way through).
    for matcher in override.get("skip_if") or []:
        for field, skip_value in matcher.items():
            api_f = camel(field)
            item[api_f] = (not skip_value) if isinstance(skip_value, bool) else None

    return item


# ---------------------------------------------------------------------------
# Conformance check (synthesize -> transform -> typecheck)
# ---------------------------------------------------------------------------

def conformance_check(resource_type):
    """Run the full pipeline over 3 synthesized items and verify structurally.

    Returns (ok, detail). ok is True only when:
      * transform produces a non-empty items map with unique keys,
      * every item type-checks against the schema with ZERO mismatches,
      * render_imports succeeds (every import-template field is present),
      * render_tfvars is byte-identical across a double run (determinism).
    detail is a human-readable failure string when ok is False, else "".
    """
    from tools.transform import (
        load_override,
        render_imports,
        render_tfvars,
        transform_items,
    )
    from tools.typecheck import check_item

    override = load_override(resource_type)
    block = load_resource(resource_type)["block"]

    raw = [synthesize_item(resource_type, seed) for seed in (1, 2, 3)]

    try:
        items, originals, drops = transform_items(raw, resource_type, override)
    except Exception as exc:
        return False, "transform raised: %s" % exc

    if not items:
        return False, "transform produced no items"
    if len(items) != len(raw):
        return False, "expected %d unique items, got %d (key collision?)" % (
            len(raw), len(items),
        )

    mismatches = []
    for key in sorted(items):
        for _, field_path, expected, got in check_item(items[key], block):
            mismatches.append((key, field_path, expected, got))
    if mismatches:
        lines = [
            "  items.%s.%s: expected %r, got %s" % (k, fp, exp, got)
            for k, fp, exp, got in mismatches
        ]
        return False, "%d typecheck mismatch(es):\n%s" % (
            len(mismatches), "\n".join(lines),
        )

    try:
        imports = render_imports(resource_type, originals, override)
    except Exception as exc:
        return False, "render_imports raised: %s" % exc
    if "module.%s." % resource_type not in imports:
        return False, "render_imports produced no import blocks"

    # Determinism: a second independent run must be byte-identical.
    again, _, _ = transform_items(
        [synthesize_item(resource_type, seed) for seed in (1, 2, 3)],
        resource_type,
        override,
    )
    if render_tfvars(items) != render_tfvars(again):
        return False, "non-deterministic transform output across runs"

    return True, ""


# ---------------------------------------------------------------------------
# CLI: on-demand module-by-module PASS/FAIL report
# ---------------------------------------------------------------------------

def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    failures = 0
    types = generated_types()
    if not types:
        # checking nothing is never success — a broken registry must not
        # render as "0 failures" green
        sys.stdout.write("error: registry has no generated types — check "
                         "tools/registry.json\n")
        return 1
    for rt in types:
        ok, detail = conformance_check(rt)
        if ok:
            sys.stdout.write("PASS %s\n" % rt)
        else:
            failures += 1
            sys.stdout.write("FAIL %s\n%s\n" % (rt, detail))
    sys.stdout.write(
        "\n%d resource(s) conformance-checked, %d failure(s)\n"
        % (len(types), failures)
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
