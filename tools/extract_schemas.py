"""Split `terraform providers schema -json` output into per-provider files.

Reads the combined schema JSON on stdin and writes
schemas/provider/<name>.json for each provider in PROVIDERS, with sorted
keys and a trailing newline so regeneration is byte-deterministic.

Run from the repo root via `make schemas`. Stdlib-only, Python 3.6-floor
syntax — see AGENTS.md rule 5.
"""
import json
import os
import sys

PROVIDERS = {
    "registry.terraform.io/zscaler/zia": "zia",
    "registry.terraform.io/zscaler/zpa": "zpa",
    "registry.terraform.io/zscaler/zcc": "zcc",
}

OUT_DIR = os.path.join("schemas", "provider")


def split_schemas(combined):
    """Return {short_name: provider_schema_subtree} for known providers.

    Raises KeyError if an expected provider is missing from the input,
    so a botched extraction can never silently produce empty dumps.
    """
    available = combined.get("provider_schemas", {})
    result = {}
    for address, name in PROVIDERS.items():
        if address not in available:
            raise KeyError("provider %s missing from schema input" % address)
        result[name] = available[address]
    return result


def main():
    combined = json.load(sys.stdin)
    os.makedirs(OUT_DIR, exist_ok=True)
    schemas = split_schemas(combined)
    for name in sorted(schemas):
        path = os.path.join(OUT_DIR, name + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(schemas[name], f, indent=2, sort_keys=True)
            f.write("\n")
        sys.stderr.write("wrote %s\n" % path)


if __name__ == "__main__":
    main()
