"""Type-check committed config files against provider schemas.

Stdlib-only, Python 3.6-floor — no jsonschema dependency.
Complements the dev-only validate-config by reporting EVERY mismatch
across EVERY resource in one pass with a suggested fix per line.
See RUNBOOK.md for usage context.
"""
import json
import os
import sys

from tools.registry import generated_types
from tools.tfschema import classify_attributes, load_resource


# ---------------------------------------------------------------------------
# Core type-checking
# ---------------------------------------------------------------------------

def check_value(value, encoding):
    """Check one value against a Terraform type encoding.

    Returns a list of (suffix_path, expected, got_repr) tuples.
    An empty list means the value passes.
    suffix_path is relative to the field that was checked (empty string for
    the field itself, or e.g. '[1]' / '[0].from' for a nested mismatch).
    """
    if value is None:
        return []

    # Primitive types
    if isinstance(encoding, str):
        if encoding == "string":
            if isinstance(value, bool) or isinstance(value, (int, float)):
                return [("", encoding, type(value).__name__)]
            return []
        if encoding == "number":
            if isinstance(value, bool):
                return [("", encoding, type(value).__name__)]
            if isinstance(value, (int, float)):
                return []
            return [("", encoding, type(value).__name__)]
        if encoding == "bool":
            if isinstance(value, bool):
                return []
            return [("", encoding, type(value).__name__)]
        return []

    if not (isinstance(encoding, list) and len(encoding) == 2):
        return []

    kind, inner = encoding

    # map type
    if kind == "map":
        if not isinstance(value, dict):
            return [("", encoding, type(value).__name__)]
        issues = []
        for k, v in value.items():
            for suffix, exp, got in check_value(v, inner):
                issues.append(("[%r]%s" % (k, suffix), exp, got))
        return issues

    # list / set of primitives
    if kind in ("list", "set") and isinstance(inner, str):
        if not isinstance(value, list):
            return [("", encoding, type(value).__name__)]
        issues = []
        for i, elem in enumerate(value):
            for suffix, exp, got in check_value(elem, inner):
                issues.append(("[%d]%s" % (i, suffix), exp, got))
        return issues

    # list / set of objects
    if kind in ("list", "set") and isinstance(inner, list) and len(inner) == 2 and inner[0] == "object":
        if not isinstance(value, list):
            return [("", encoding, type(value).__name__)]
        members = inner[1]
        issues = []
        for i, elem in enumerate(value):
            if not isinstance(elem, dict):
                issues.append(("[%d]" % i, encoding, type(elem).__name__))
                continue
            for mk, mv in elem.items():
                if mk not in members:
                    issues.append(("[%d].%s" % (i, mk), "not an input", type(mv).__name__))
                    continue
                for suffix, exp, got in check_value(mv, members[mk]):
                    issues.append(("[%d].%s%s" % (i, mk, suffix), exp, got))
        return issues

    return []


def check_item(item, block):
    """Walk one item's keys against a block schema.

    Returns list of (item_key_placeholder, field_path, expected, got_repr).
    item_key_placeholder is always "" here; callers supply the real item key.
    """
    attrs = block.get("attributes") or {}
    block_types = block.get("block_types") or {}
    cls = classify_attributes(block)
    valid_attrs = set(cls["required"] + cls["optional"])

    issues = []

    for key in sorted(item):
        value = item[key]

        if key in block_types:
            if value is None:
                continue
            if not isinstance(value, list):
                issues.append(("", key, "list of blocks", type(value).__name__))
                continue
            inner_block = block_types[key]["block"]
            for i, elem in enumerate(value):
                if not isinstance(elem, dict):
                    issues.append(("", "%s[%d]" % (key, i), "object", type(elem).__name__))
                    continue
                for _, sub_path, exp2, got2 in check_item(elem, inner_block):
                    issues.append(("", "%s[%d].%s" % (key, i, sub_path), exp2, got2))
            continue

        if key not in valid_attrs:
            issues.append(("", key, "not an input — transform should have dropped it", type(value).__name__))
            continue

        enc = attrs.get(key, {}).get("type")
        if enc is None:
            continue
        for suffix, exp, got in check_value(value, enc):
            issues.append(("", key + suffix, exp, got))

    return issues


def check_config_file(resource_type, path):
    """Load a tfvars JSON file and type-check every item.

    Returns list of (item_key, field_path, expected, got_repr) tuples.
    """
    with open(path) as f:
        data = json.load(f)
    items = data.get("items") or {}
    rs = load_resource(resource_type)
    block = rs["block"]

    results = []
    for item_key in sorted(items):
        item = items[item_key]
        for _, field_path, expected, got_repr in check_item(item, block):
            results.append((item_key, field_path, expected, got_repr))
    return results


# ---------------------------------------------------------------------------
# Remediation suggestions
# ---------------------------------------------------------------------------

def suggest(expected, got_repr, field_path):
    """One-line remediation suggestion for a single mismatch.

    Parameters match the expected/got_repr columns from check_item.
    """
    if expected == "bool":
        if got_repr == "int":
            return "re-run make transform (0/1 ints coerce since PR22); other ints: relay the field"
        if got_repr == "str":
            return "API uses literal strings; if 'yes'/'no'-style, relay the field for a value map"
        return "relay this line"

    if isinstance(expected, list) and len(expected) == 2 and expected[0] in ("list", "set"):
        if got_repr == "str":
            return "add %r to split_csv in tools/overrides/<rt>.json" % field_path
        return "relay this line"

    if expected == "string":
        if got_repr in ("int", "float"):
            return "re-run make transform (numbers coerce to string)"
        return "relay this line"

    if expected == "number":
        if got_repr == "str":
            return "re-run make transform"
        return "relay this line"

    if "not an input" in str(expected):
        return "regenerate config (make transform) — stale file or missing schema filter"

    return "relay this line"


def _truncate(text, n=40):
    s = repr(text)
    if len(s) > n:
        s = s[:n - 3] + "..."
    return s


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    """argv = [tenant]; iterate generated_types(); skip types without config
    file; print report; exit 1 on mismatches, 0 on clean."""
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) != 1:
        sys.stderr.write("usage: python -m tools.typecheck <tenant>\n")
        return 2

    tenant = argv[0]
    config_dir = os.path.join("config", tenant)

    all_mismatches = []
    resource_types_checked = []

    for rt in generated_types():
        path = os.path.join(config_dir, rt + ".auto.tfvars.json")
        if not os.path.exists(path):
            continue
        resource_types_checked.append(rt)
        try:
            mismatches = check_config_file(rt, path)
        except Exception as exc:
            sys.stderr.write("error reading %s: %s\n" % (path, exc))
            continue
        for item_key, field_path, expected, got_repr in mismatches:
            suggestion = suggest(expected, got_repr, field_path.split(".")[-1])
            line = "%s: items.%s.%s: expected %r, got %s — %s" % (
                rt,
                item_key,
                field_path,
                expected,
                _truncate(got_repr),
                suggestion,
            )
            sys.stdout.write(line + "\n")
            all_mismatches.append(line)

    if not resource_types_checked:
        # Checking nothing is never success — a typo'd label or a skipped
        # `make transform` should fail loudly, not pass silently.
        sys.stdout.write(
            "error: no config files found for tenant %r in %s "
            "(typo? run make transform first)\n" % (tenant, config_dir)
        )
        return 1

    n = len(all_mismatches)
    m = len(resource_types_checked)

    if n:
        sys.stdout.write("\n%d mismatch(es) across %d resource(s)\n" % (n, m))
        return 1

    sys.stdout.write(
        "all %d config file%s type-check clean\n"
        % (m, "s" if m != 1 else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
