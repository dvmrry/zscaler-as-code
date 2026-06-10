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
        return value
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
