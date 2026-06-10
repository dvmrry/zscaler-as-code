"""Single source of truth for which resource types the toolchain knows.

Replaces the old tools/resources.txt (generator list) and
tools/fetch_manifest.json (fetch endpoints). Consumers read the slice they
need: generators use generated_types(); the fetcher uses fetch_entry().
Stdlib-only, Python 3.6-floor — see AGENTS.md rule 5.
"""
import json
import os

REGISTRY_PATH = os.path.join("tools", "registry.json")

_cache = {}


def load_registry():
    if not _cache:
        with open(REGISTRY_PATH) as f:
            _cache.update(json.load(f))
    return _cache


def generated_types():
    """Resource types with generate=true, sorted."""
    reg = load_registry()
    return sorted(rt for rt, e in reg.items() if e.get("generate"))


def fetch_entry(resource_type):
    """Flattened fetch config {product, path, pagination, query?} for a
    resource, or KeyError if it has no fetch wiring."""
    reg = load_registry()
    if resource_type not in reg or "fetch" not in reg[resource_type]:
        raise KeyError(
            "%r has no fetch entry in tools/registry.json" % resource_type
        )
    entry = dict(reg[resource_type]["fetch"])
    entry["product"] = reg[resource_type]["product"]
    return entry
