"""Shared schema-reading core for the generators.

Loads the committed provider schema dumps and answers structural
questions: which provider owns a resource type, how attributes classify
(required / optional / computed-only), and how Terraform type encodings
map to HCL type expressions and JSON Schema fragments.

Stdlib-only, Python 3.6-floor syntax — see AGENTS.md rule 5.
"""
import json
import os

SCHEMA_DIR = os.path.join("schemas", "provider")
PROVIDER_PREFIXES = {"zia_": "zia", "zpa_": "zpa", "zcc_": "zcc"}

_cache = {}


def _provider_for(resource_type):
    for prefix, provider in PROVIDER_PREFIXES.items():
        if resource_type.startswith(prefix):
            return provider
    raise KeyError("resource type %r has no known provider prefix" % resource_type)


def load_provider(provider):
    if provider not in _cache:
        path = os.path.join(SCHEMA_DIR, provider + ".json")
        with open(path, encoding="utf-8") as f:
            _cache[provider] = json.load(f)
    return _cache[provider]


def load_resource(resource_type):
    """Return the schema entry for one resource type.

    Raises KeyError for unknown prefixes or resource types so a typo in
    tools/registry.json fails the build instead of generating nothing.
    """
    provider = _provider_for(resource_type)
    schemas = load_provider(provider)["resource_schemas"]
    if resource_type not in schemas:
        raise KeyError("resource type %r not in %s schema" % (resource_type, provider))
    return schemas[resource_type]


def block_is_single(block_type):
    """True when a nested block holds at most ONE instance: nesting_mode
    "single", or a list/set block the provider caps with max_items=1 (the
    ZIA ID-group pattern — one block whose members are lists, e.g.
    departments { id = [...] }; terraform core rejects a second block).

    Single-instance blocks are OBJECTS in every generated contract —
    variables.tf type, JSON Schema, transform output, typecheck — and the
    generated main.tf wraps [x] at plan time. Every layer must call THIS
    predicate rather than reading nesting_mode directly, so the layers
    cannot disagree about which blocks are objects.
    """
    if block_type["nesting_mode"] == "single":
        return True
    return block_type.get("max_items") == 1


def classify_attributes(block):
    """Split a block's attributes into required / optional / computed_only.

    required: must be supplied. optional: may be supplied (covers
    optional+computed). computed_only: provider-populated, excluded from
    input. All lists sorted for deterministic rendering. Fails loudly on
    plugin-framework nested_type attributes — none exist in the pinned
    schemas, and silent mishandling would corrupt generated modules.

    Provider-DEPRECATED non-required attributes are treated as computed_only:
    excluded from input everywhere (modules, config JSON Schema, typecheck,
    transform) so we never write a dying field. Setting one emits a provider
    deprecation warning on every plan (e.g. zpa_policy_access_rule.rule_order,
    deprecated in favor of the zpa_policy_access_rule_reorder resource); these
    are computed, so the provider still populates them. A deprecated *required*
    attribute is kept (the resource can't be created without it) — add an
    override if one ever appears.

    A top-level computed `id` (the resource identity) is NOT excluded here —
    this classifier runs on nested blocks too, where a computed `id` is a
    legitimate reference input (e.g. zia_location_management's vpn_credentials).
    For a RESOURCE's top-level block call resource_input_attrs() instead, which
    drops that identity id so the module, the config JSON Schema, and typecheck
    all agree on the inputs.
    """
    out = {"required": [], "optional": [], "computed_only": []}
    for name, attr in sorted((block.get("attributes") or {}).items()):
        if attr.get("deprecated") and not attr.get("required"):
            out["computed_only"].append(name)
        elif attr.get("required"):
            out["required"].append(name)
        elif attr.get("optional"):
            out["optional"].append(name)
        else:
            out["computed_only"].append(name)
    return out


def resource_input_attrs(block):
    """classify_attributes for a RESOURCE's TOP-LEVEL block, minus the resource
    identity: a computed top-level `id` is provider-populated and rejected as
    an input (zpa_policy_access_rule_reorder errors "Invalid or unknown key").
    Use classify_attributes directly for NESTED blocks, where a computed `id`
    is a real reference input. Shared by the module generator, the config JSON
    Schema, and typecheck so the gates and the module agree on the inputs."""
    cls = classify_attributes(block)
    attrs = block.get("attributes") or {}
    if "id" in cls["optional"] and attrs.get("id", {}).get("computed"):
        return {
            "required": cls["required"],
            "optional": [n for n in cls["optional"] if n != "id"],
            "computed_only": cls["computed_only"] + ["id"],
        }
    return cls


def attr_type(attr):
    """Return a Terraform type encoding for either SDKv2 or framework attrs."""
    if "type" in attr:
        return attr["type"]
    if "nested_type" in attr:
        return nested_type_encoding(attr["nested_type"])
    raise ValueError("attribute has no type or nested_type: %r" % attr)


def nested_type_encoding(nested_type):
    """Convert plugin-framework nested_type metadata into our type encoding."""
    members = {}
    for name, attr in sorted((nested_type.get("attributes") or {}).items()):
        if attr.get("deprecated") and not attr.get("required"):
            continue
        if attr.get("required") or attr.get("optional"):
            members[name] = attr_type(attr)
    enc = ["object", members]
    mode = nested_type.get("nesting_mode")
    if mode == "single":
        return enc
    if mode in ("list", "set", "map"):
        return [mode, enc]
    raise ValueError("unsupported nested_type nesting_mode %r" % mode)


def block_has_inputs(block):
    """True when a nested block has at least one settable input."""
    cls = classify_attributes(block)
    if cls["required"] or cls["optional"]:
        return True
    for bt in (block.get("block_types") or {}).values():
        if block_has_inputs(bt["block"]):
            return True
    return False


def input_block_types(block):
    """Nested blocks that are real inputs, excluding computed-only blocks."""
    return dict(
        (name, bt)
        for name, bt in sorted((block.get("block_types") or {}).items())
        if block_has_inputs(bt["block"])
    )


def _encoding_has_sensitive(encoding, attr=None):
    if attr is not None and attr.get("sensitive"):
        return True
    if isinstance(encoding, list) and len(encoding) == 2:
        kind, inner = encoding
        if kind in ("list", "set", "map"):
            return _encoding_has_sensitive(inner)
        if kind == "object" and isinstance(inner, dict):
            return any(_encoding_has_sensitive(v) for v in inner.values())
    return False


def block_has_sensitive(block):
    for attr in (block.get("attributes") or {}).values():
        if _encoding_has_sensitive(attr_type(attr), attr):
            return True
    for bt in (block.get("block_types") or {}).values():
        if block_has_sensitive(bt["block"]):
            return True
    return False


_PRIMITIVES_HCL = {"string": "string", "bool": "bool", "number": "number"}
_PRIMITIVES_JSON = {"string": "string", "bool": "boolean", "number": "number"}


def hcl_type(encoding, indent=4):
    """Terraform JSON type encoding -> HCL type expression.

    Object members render one per line, sorted, each wrapped optional()
    (SDKv2 object-typed attributes carry no per-member requiredness).
    indent: spaces of the surrounding context, for stable nesting.
    """
    if isinstance(encoding, str):
        if encoding in _PRIMITIVES_HCL:
            return _PRIMITIVES_HCL[encoding]
        raise ValueError("unsupported primitive type encoding: %r" % encoding)
    if isinstance(encoding, list) and len(encoding) == 2:
        kind, inner = encoding
        if kind == "object" and isinstance(inner, dict):
            pad = " " * (indent + 2)
            lines = [
                "%s%s = optional(%s)" % (pad, name, hcl_type(inner[name], indent + 2))
                for name in sorted(inner)
            ]
            return "object({\n%s\n%s})" % ("\n".join(lines), " " * indent)
        if kind in ("list", "set", "map") and isinstance(inner, str):
            return "%s(%s)" % (kind, hcl_type(inner))
        if kind in ("list", "set", "map") and isinstance(inner, list):
            return "%s(%s)" % (kind, hcl_type(inner, indent))
    raise ValueError("unsupported type encoding: %r" % (encoding,))


def json_schema_type(encoding):
    """Terraform JSON type encoding -> JSON Schema fragment (dict)."""
    if isinstance(encoding, str):
        if encoding in _PRIMITIVES_JSON:
            return {"type": _PRIMITIVES_JSON[encoding]}
        raise ValueError("unsupported primitive type encoding: %r" % encoding)
    if isinstance(encoding, list) and len(encoding) == 2:
        kind, inner = encoding
        if kind == "object" and isinstance(inner, dict):
            return {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    name: json_schema_type(inner[name])
                    for name in sorted(inner)
                },
            }
        if kind == "map":
            return {"type": "object", "additionalProperties": json_schema_type(inner)}
        if kind in ("list", "set"):
            if isinstance(inner, str):
                frag = {"type": "array", "items": json_schema_type(inner)}
                if kind == "set":
                    frag["uniqueItems"] = True
                return frag
            if isinstance(inner, list):
                frag = {"type": "array", "items": json_schema_type(inner)}
                if kind == "set":
                    frag["uniqueItems"] = True
                return frag
    raise ValueError("unsupported type encoding: %r" % (encoding,))
