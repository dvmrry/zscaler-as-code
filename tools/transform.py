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
