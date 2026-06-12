"""Sweep the ENTIRE SDK <-> terraform surface — no tenant data needed.

The vendor's Go SDK structs ARE the modeled API surface. For every
generated resource this tool parses the pinned-tag SDK structs (json
tags, recursively), synthesizes a MAXIMAL item carrying every field
the vendor models, runs it through the real transform, and classifies
every resulting drop with the triage classifier. The result is the
drop report for a tenant that uses every feature — before any real
tenant returns any of it.

Run it once to pre-acknowledge the whole current surface, and at every
provider/SDK bump (RUNBOOK: Provider Bumps): NEW SDK fields show up
here with zero data and zero lead-time dependency on tenants.

What it cannot see: fields the API returns that the SDK does not model
(the applications[].enabled class) — those still arrive via the
DROPS_CHECK tripwire on real pulls, pre-classified by make triage.

APPLY=1 writes the safe classes to acknowledged_drops (same rules as
make triage; SYNONYM/UNKNOWN are never auto-acknowledged). Exit: 0 =
nothing needs eyes; 4 = worklist remains; 1 = failure. Needs network.

Stdlib-only, Python 3.6-floor — see AGENTS.md rule 5.
"""
import json
import os
import re
import sys

from tools.registry import generated_types
from tools.tfschema import load_resource
from tools.transform import load_override, snake, transform_items
from tools.triage import (
    SAFE_CLASSES, SDK_PATHS, acknowledge, classify, fetch_sdk_text,
)

_STRUCT_RE = re.compile(r'type (\w+) struct \{(.*?)\n\}', re.DOTALL)
_FIELD_RE = re.compile(r'^\s*(\w+)\s+([\[\]\*\w\.{}]+)\s+`[^`]*json:"(\w+)',
                       re.MULTILINE)
_MAX_DEPTH = 3


def parse_structs(go_text):
    """Go source -> {struct_name: [(json_tag, go_type), ...]}."""
    registry = {}
    for m in _STRUCT_RE.finditer(go_text):
        name, body = m.group(1), m.group(2)
        registry[name] = [(f.group(3), f.group(2))
                         for f in _FIELD_RE.finditer(body)]
    return registry


def synth_value(go_type, registry, depth):
    """A placeholder value for a Go type. Unresolvable struct types get
    the reference shape ({id, name}) — the commonest cross-package case
    (common.IDNameExtensions and friends)."""
    t = go_type
    if t.startswith("[]"):
        return [synth_value(t[2:], registry, depth)]
    if t.startswith("*"):
        return synth_value(t[1:], registry, depth)
    if "." in t:
        t = t.split(".")[-1]
    if t in registry:
        if depth >= _MAX_DEPTH:
            return {"id": "1"}
        return synth_struct(t, registry, depth + 1)
    low = t.lower()
    if low == "bool":
        return True
    if low.startswith("int") or low.startswith("uint") or low.startswith("float"):
        return 1
    if low == "string":
        return "x"
    if t.startswith("map["):
        return {}
    if t == "interface{}":
        return "x"
    return {"id": "1", "name": "x"}


def synth_struct(name, registry, depth=0):
    item = {}
    for tag, go_type in registry.get(name, []):
        item[tag] = synth_value(go_type, registry, depth)
    return item


def pick_root(registry, rt):
    """The resource's main struct = the one whose snake'd tags best
    overlap the provider schema's field names."""
    blk = load_resource(rt)["block"]
    schema_names = set(blk.get("attributes") or {}) | set(
        blk.get("block_types") or {})
    best, best_score = None, -1
    for name, fields in sorted(registry.items()):
        score = len({snake(tag) for tag, _ in fields} & schema_names)
        if score > best_score:
            best, best_score = name, score
    return best


def neutralize_skip_if(item, override):
    """skip_if matchers would silently exclude the synthetic item (e.g.
    predefined=true) — flip matched values so it always transforms."""
    matchers = override.get("skip_if") or []
    snake_to_tag = {snake(tag): tag for tag in item}
    for matcher in matchers:
        for field, value in matcher.items():
            tag = snake_to_tag.get(field)
            if tag is not None and item.get(tag) == value:
                item[tag] = (not value) if isinstance(value, bool) else "x-no"
    return item


def sweep(rt, sdk_text):
    """-> (drop_paths, field_count) for one resource's maximal item."""
    registry = parse_structs(sdk_text)
    root = pick_root(registry, rt)
    if root is None:
        return None, 0
    override = load_override(rt)
    item = neutralize_skip_if(synth_struct(root, registry), override)
    _, _, reported = transform_items([item], rt, override)
    return reported, len(item)


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if argv:
        sys.stderr.write("usage: python -m tools.surface  "
                         "(make surface [APPLY=1])\n")
        return 2
    apply_safe = bool(os.environ.get("APPLY"))
    worklist = 0
    acknowledged = 0
    skipped = []
    for rt in generated_types():
        if rt not in SDK_PATHS:
            skipped.append(rt)
            continue
        try:
            sdk_text = fetch_sdk_text(rt)
        except Exception as exc:
            sys.stderr.write("error: cannot fetch SDK source for %s: %s\n"
                             "hint: network/proxy — HTTPS_PROXY + "
                             "REQUESTS_CA_BUNDLE apply, same as make fetch\n"
                             % (rt, exc))
            return 1
        reported, width = sweep(rt, sdk_text)
        if reported is None:
            skipped.append(rt)
            continue
        sys.stdout.write("== %s: %d SDK-modeled field(s), %d dropped "
                         "unacknowledged\n" % (rt, width, len(reported)))
        safe = []
        for path in reported:
            klass, why = classify(rt, path, sdk_text)
            sys.stdout.write("SURFACE %s: %s: %s — %s\n"
                             % (rt, path, klass, why))
            if klass in SAFE_CLASSES:
                safe.append(path)
            else:
                worklist += 1
        if safe and apply_safe:
            acknowledge(rt, safe)
            acknowledged += len(safe)
    for rt in skipped:
        sys.stdout.write("== %s: no SDK mapping — extend SDK_PATHS in "
                         "tools/triage.py\n" % rt)
    if acknowledged:
        sys.stdout.write("\n%d safe path(s) written to acknowledged_drops "
                         "— commit the override changes\n" % acknowledged)
    elif not apply_safe:
        sys.stdout.write("\n(dry run — APPLY=1 writes the safe classes)\n")
    sys.stdout.write("%d path(s) need eyes (SYNONYM/UNKNOWN)\n" % worklist)
    if worklist:
        sys.stdout.write("tools/MINING.md has the per-path procedure; never "
                         "acknowledge a SYNONYM without checking the "
                         "provider read/expand.\n")
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
