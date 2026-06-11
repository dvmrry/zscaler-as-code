"""Derive (tenant, resource_type) plan targets from a git diff.

The repo layout encodes the mapping: a change to
config/<tenant>/<type>.auto.tfvars.json (or the matching imports/ or
envs/ paths) affects exactly one (tenant, type) pair. A change to
modules/<type>/ affects that type on every tenant that has config for
it. A change to the shared machinery (tools/, schemas/, Makefile)
affects everything, so every configured pair is emitted — plans are
read-only, so over-planning is safe; under-planning is not.

Output: one "tenant resource_type" line per pair, sorted. Empty output
with exit 0 means the diff touched nothing plannable (docs-only change)
— a PR pipeline must treat that as success, not failure.

Stdlib-only, Python 3.6-floor — see AGENTS.md rule 5.
"""
import os
import subprocess
import sys

from tools.registry import generated_types

CONFIG_SUFFIX = ".auto.tfvars.json"
IMPORTS_SUFFIX = "_imports.tf"
GLOBAL_PREFIXES = ("tools/", "schemas/", "Makefile")


def discover_config_pairs(config_root="config"):
    """All (tenant, resource_type) pairs with a committed config file."""
    pairs = set()
    if not os.path.isdir(config_root):
        return pairs
    types = set(generated_types())
    for tenant in sorted(os.listdir(config_root)):
        tdir = os.path.join(config_root, tenant)
        if not os.path.isdir(tdir):
            continue
        for fname in sorted(os.listdir(tdir)):
            if fname.endswith(CONFIG_SUFFIX):
                rt = fname[: -len(CONFIG_SUFFIX)]
                if rt in types:
                    pairs.add((tenant, rt))
    return pairs


def pairs_from_paths(paths, config_pairs):
    """Map changed file paths to the (tenant, type) pairs they affect.

    config_pairs bounds every expansion: a pair is only ever emitted if
    the tenant actually has config for that type, so renames/typos can't
    fan out into nonexistent roots.
    """
    pairs = set()
    for path in paths:
        parts = path.split("/")
        if path.startswith("config/") and len(parts) == 3 and parts[2].endswith(CONFIG_SUFFIX):
            pair = (parts[1], parts[2][: -len(CONFIG_SUFFIX)])
            if pair in config_pairs:
                pairs.add(pair)
        elif path.startswith("imports/") and len(parts) == 3 and parts[2].endswith(IMPORTS_SUFFIX):
            pair = (parts[1], parts[2][: -len(IMPORTS_SUFFIX)])
            if pair in config_pairs:
                pairs.add(pair)
        elif path.startswith("envs/") and len(parts) >= 3:
            pair = (parts[1], parts[2])
            if pair in config_pairs:
                pairs.add(pair)
        elif path.startswith("modules/") and len(parts) >= 2:
            rt = parts[1]
            for tenant, pair_rt in config_pairs:
                if pair_rt == rt:
                    pairs.add((tenant, rt))
        elif path.startswith(GLOBAL_PREFIXES):
            # Shared machinery changed: every configured pair is in scope.
            return set(config_pairs)
    return pairs


def changed_paths(base_ref):
    """Changed files vs the merge-base with base_ref (three-dot diff —
    exactly what a PR pipeline wants: the branch's own changes only)."""
    out = subprocess.check_output(
        ["git", "diff", "--name-only", base_ref + "...HEAD"]
    )
    return [line for line in out.decode().splitlines() if line.strip()]


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        sys.stderr.write("usage: python -m tools.changed <base-ref>\n")
        return 2
    try:
        paths = changed_paths(argv[0])
    except subprocess.CalledProcessError:
        sys.stderr.write(
            "error: git diff against %r failed (unknown ref? shallow clone "
            "without the base? fetch it first)\n" % argv[0]
        )
        return 2
    pairs = pairs_from_paths(paths, discover_config_pairs())
    for tenant, rt in sorted(pairs):
        sys.stdout.write("%s %s\n" % (tenant, rt))
    if not pairs:
        sys.stderr.write("no plannable changes vs %s\n" % argv[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
