"""Generate JSON Schemas for tfvars config files from provider dumps.

One schema per resource type in tools/registry.json, written to
schemas/tfvars/<type>.schema.json — used for editor autocomplete and CI
validation of config/. Authoring-side; output committed. Stdlib-only,
Python 3.6-floor — see AGENTS.md rule 5.
"""
import json
import os
import sys

from tools.registry import generated_types
from tools.tfschema import (
    attr_type,
    block_is_single,
    classify_attributes,
    input_block_types,
    json_schema_type,
    load_resource,
    resource_input_attrs,
)

OUT_DIR = os.path.join("schemas", "tfvars")


def _block_schema(block, top_level=False):
    # Top level: drop the resource-identity id so the schema matches the module
    # (which rejects it as an input) and typecheck.
    cls = resource_input_attrs(block) if top_level else classify_attributes(block)
    props = {}
    required = list(cls["required"])
    for name in cls["required"] + cls["optional"]:
        props[name] = json_schema_type(attr_type(block["attributes"][name]))
    for name, bt in input_block_types(block).items():
        inner = _block_schema(bt["block"])
        min_items = bt.get("min_items") or 0
        if block_is_single(bt):
            props[name] = inner
        elif bt["nesting_mode"] == "set":
            props[name] = {"type": "array", "items": inner, "uniqueItems": True}
        else:
            props[name] = {"type": "array", "items": inner}
        if min_items >= 1:
            # required (min_items>=1) block: enforce presence + count so the
            # schema rejects what the provider would (e.g. reorder `rules`).
            required.append(name)
            if props[name].get("type") == "array":
                props[name]["minItems"] = min_items
    out = {"type": "object", "additionalProperties": False, "properties": props}
    if required:
        out["required"] = sorted(required)
    return out


def build_schema(resource_type, resource_schema):
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "%s tfvars" % resource_type,
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "items": {
                "type": "object",
                "additionalProperties": _block_schema(
                    resource_schema["block"], top_level=True),
            }
        },
        "required": ["items"],
    }


def build_editor_settings():
    """VS Code json.schemas mapping for every generated resource type.

    Written as a committed REFERENCE file (schemas/tfvars/
    vscode.settings.example.json) for operators to merge into their own
    .vscode/settings.json — never written into .vscode/ directly, which
    would clobber personal settings. Editing config by hand then gets
    field autocomplete and inline validation from the same schemas CI
    validates with.
    """
    return {
        "json.schemas": [
            {
                "fileMatch": ["config/*/%s.auto.tfvars.json" % rt],
                "url": "./schemas/tfvars/%s.schema.json" % rt,
            }
            for rt in generated_types()
        ]
    }


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    for resource_type in generated_types():
        schema = build_schema(resource_type, load_resource(resource_type))
        path = os.path.join(OUT_DIR, resource_type + ".schema.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(schema, f, indent=2, sort_keys=True)
            f.write("\n")
        sys.stderr.write("wrote %s\n" % path)
    settings_path = os.path.join(OUT_DIR, "vscode.settings.example.json")
    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(build_editor_settings(), f, indent=2, sort_keys=True)
        f.write("\n")
    sys.stderr.write("wrote %s\n" % settings_path)


if __name__ == "__main__":
    main()
