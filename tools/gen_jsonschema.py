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
from tools.tfschema import classify_attributes, json_schema_type, load_resource

OUT_DIR = os.path.join("schemas", "tfvars")


def _block_schema(block):
    cls = classify_attributes(block)
    props = {}
    for name in cls["required"] + cls["optional"]:
        props[name] = json_schema_type(block["attributes"][name]["type"])
    for name, bt in sorted((block.get("block_types") or {}).items()):
        inner = _block_schema(bt["block"])
        if bt["nesting_mode"] == "single":
            props[name] = inner
        elif bt["nesting_mode"] == "set":
            props[name] = {"type": "array", "items": inner, "uniqueItems": True}
        else:
            props[name] = {"type": "array", "items": inner}
    out = {"type": "object", "additionalProperties": False, "properties": props}
    if cls["required"]:
        out["required"] = cls["required"]
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
                "additionalProperties": _block_schema(resource_schema["block"]),
            }
        },
        "required": ["items"],
    }


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    for resource_type in generated_types():
        schema = build_schema(resource_type, load_resource(resource_type))
        path = os.path.join(OUT_DIR, resource_type + ".schema.json")
        with open(path, "w") as f:
            json.dump(schema, f, indent=2, sort_keys=True)
            f.write("\n")
        sys.stderr.write("wrote %s\n" % path)


if __name__ == "__main__":
    main()
