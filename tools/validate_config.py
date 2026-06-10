"""Validate config/ tfvars files against the generated JSON Schemas.

Dev-only gate (jsonschema never required in restricted environments): the
Makefile target warn-skips when the library is missing. Pure pairing logic
is testable without it. Stdlib 3.6-floor except the optional import.
"""
import json
import os
import sys

CONFIG_ROOT = "config"
SCHEMAS_ROOT = os.path.join("schemas", "tfvars")


def config_pairs(config_root=CONFIG_ROOT, schemas_root=SCHEMAS_ROOT):
    """(tenant, resource_type, config_path, schema_path) for every config
    file. SystemExit if a config file has no generated schema — an unknown
    resource type in config/ is always a mistake."""
    pairs = []
    if not os.path.isdir(config_root):
        return pairs
    for tenant in sorted(os.listdir(config_root)):
        tdir = os.path.join(config_root, tenant)
        if not os.path.isdir(tdir):
            continue
        for fname in sorted(os.listdir(tdir)):
            if not fname.endswith(".auto.tfvars.json"):
                continue
            rt = fname[: -len(".auto.tfvars.json")]
            schema = os.path.join(schemas_root, rt + ".schema.json")
            if not os.path.exists(schema):
                raise SystemExit(
                    "config %s/%s has no generated schema %s — unknown "
                    "resource type?" % (tenant, fname, schema)
                )
            pairs.append((tenant, rt, os.path.join(tdir, fname), schema))
    return pairs


def main():
    import jsonschema  # dev-only; Makefile warn-skips when missing
    failures = 0
    for tenant, rt, cfg_path, schema_path in config_pairs():
        with open(cfg_path) as f:
            data = json.load(f)
        with open(schema_path) as f:
            schema = json.load(f)
        try:
            jsonschema.validate(data, schema)
            sys.stderr.write("ok %s/%s\n" % (tenant, rt))
        except jsonschema.ValidationError as e:
            failures += 1
            sys.stderr.write("FAIL %s/%s: %s\n" % (tenant, rt, e.message))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
