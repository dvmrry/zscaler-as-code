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
PROVIDER_PREFIXES = {"zia_": "zia", "zpa_": "zpa"}

_cache = {}


def _provider_for(resource_type):
    for prefix, provider in PROVIDER_PREFIXES.items():
        if resource_type.startswith(prefix):
            return provider
    raise KeyError("resource type %r has no known provider prefix" % resource_type)


def load_provider(provider):
    if provider not in _cache:
        path = os.path.join(SCHEMA_DIR, provider + ".json")
        with open(path) as f:
            _cache[provider] = json.load(f)
    return _cache[provider]


def load_resource(resource_type):
    """Return the schema entry for one resource type.

    Raises KeyError for unknown prefixes or resource types so a typo in
    tools/resources.txt fails the build instead of generating nothing.
    """
    provider = _provider_for(resource_type)
    schemas = load_provider(provider)["resource_schemas"]
    if resource_type not in schemas:
        raise KeyError("resource type %r not in %s schema" % (resource_type, provider))
    return schemas[resource_type]


def classify_attributes(block):
    """Split a block's attributes into required / optional / computed_only.

    required: must be supplied. optional: may be supplied (covers
    optional+computed). computed_only: provider-populated, excluded from
    input. All lists sorted for deterministic rendering. Fails loudly on
    plugin-framework nested_type attributes — none exist in the pinned
    schemas, and silent mishandling would corrupt generated modules.
    """
    out = {"required": [], "optional": [], "computed_only": []}
    for name, attr in sorted((block.get("attributes") or {}).items()):
        if "nested_type" in attr:
            raise ValueError(
                "attribute %r uses nested_type (plugin framework); "
                "the generator does not support it — add an override" % name
            )
        if attr.get("required"):
            out["required"].append(name)
        elif attr.get("optional"):
            out["optional"].append(name)
        else:
            out["computed_only"].append(name)
    return out


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
        if kind in ("list", "set", "map") and isinstance(inner, str):
            return "%s(%s)" % (kind, hcl_type(inner))
        if kind in ("list", "set") and isinstance(inner, list) and inner[0] == "object":
            members = inner[1]
            pad = " " * (indent + 2)
            lines = [
                "%s%s = optional(%s)" % (pad, name, hcl_type(members[name], indent + 2))
                for name in sorted(members)
            ]
            return "%s(object({\n%s\n%s}))" % (kind, "\n".join(lines), " " * indent)
    raise ValueError("unsupported type encoding: %r" % (encoding,))


def json_schema_type(encoding):
    """Terraform JSON type encoding -> JSON Schema fragment (dict)."""
    if isinstance(encoding, str):
        if encoding in _PRIMITIVES_JSON:
            return {"type": _PRIMITIVES_JSON[encoding]}
        raise ValueError("unsupported primitive type encoding: %r" % encoding)
    if isinstance(encoding, list) and len(encoding) == 2:
        kind, inner = encoding
        if kind == "map" and isinstance(inner, str):
            return {"type": "object", "additionalProperties": json_schema_type(inner)}
        if kind in ("list", "set"):
            if isinstance(inner, str):
                return {"type": "array", "items": json_schema_type(inner)}
            if isinstance(inner, list) and inner[0] == "object":
                members = inner[1]
                return {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            name: json_schema_type(members[name])
                            for name in sorted(members)
                        },
                    },
                }
    raise ValueError("unsupported type encoding: %r" % (encoding,))
