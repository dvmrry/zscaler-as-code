"""Targeted adoption acceptance check.

The Makefile wraps this with a live fetch. This tool then checks the pulled
JSON for unacknowledged transform drops, allowing only the repo-declared
known holds, and writes the normal transform outputs when the resource is
acceptable.

It is intentionally scoped to registered fetch-managed resources. Provider
headroom that is not in tools/registry.json yet belongs in the classify /
add-resource flow first.

Stdlib-only, Python 3.6-floor; see AGENTS.md rule 5.
"""
import json
import os
import re
import sys

from tools.adoption_status import known_holds_for, load_status
from tools.fetch import expand_selectors, load_manifest, products_in_manifest
from tools.registry import derive_entry, fetch_entry, load_registry
from tools.transform import load_override, main as transform_main, transform_items


_VALID_TENANT = re.compile(r"^[A-Za-z0-9_.-]+$")


def validate_tenant(tenant):
    if not _VALID_TENANT.match(tenant) or tenant in (".", ".."):
        raise ValueError(
            "tenant %r must match [A-Za-z0-9_.-]+ and not be '.'/'..'"
            % tenant)


def expand_resources(selectors):
    if not selectors:
        raise ValueError("adoption-check requires at least one resource/product selector")
    registry = load_registry()
    non_fetch = []
    for selector in selectors:
        entry = registry.get(selector)
        if entry and "fetch" not in entry:
            non_fetch.append(selector)
    if non_fetch:
        raise ValueError(
            "adoption-check accepts fetch-managed resources/products only; "
            "non-fetch selector(s): %s" % ", ".join(sorted(non_fetch)))
    selected = expand_selectors(selectors)
    manifest = load_manifest()
    unknown = sorted(selected - set(manifest))
    if unknown:
        raise ValueError(
            "unknown or non-fetch resource selector(s): %s (valid products: %s)"
            % (", ".join(unknown), ", ".join(products_in_manifest())))
    return sorted(selected)


def _load_raw(path):
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, list):
        raise ValueError("%s must be a JSON list; rerun make fetch" % path)
    return raw


def _known_hold_map(resource_type, status):
    out = {}
    for hold in known_holds_for(resource_type, status):
        out[hold["path"]] = hold.get("issue")
    return out


def check_resource(resource_type, tenant, pulls_dir, status, write=True):
    if derive_entry(resource_type):
        raise ValueError(
            "%r is derived/non-fetch; adoption-check only accepts fetch-managed "
            "resources" % resource_type)
    # Raises for non-fetch-managed resources: adoption-check is not the
    # add-resource classifier.
    fetch_entry(resource_type)
    path = os.path.join(pulls_dir, resource_type + ".json")
    if not os.path.exists(path):
        return {
            "resource": resource_type,
            "state": "missing-pull",
            "drops": [],
            "unexpected": [],
            "path": path,
        }
    raw = _load_raw(path)
    _, _, drops = transform_items(raw, resource_type, load_override(resource_type))
    known_holds = _known_hold_map(resource_type, status)
    known = set(known_holds)
    unexpected = sorted(set(drops) - known)
    state = "clean"
    if unexpected:
        state = "unexpected-drops"
    elif drops:
        state = "known-hold"
    if write and state != "unexpected-drops":
        code = transform_main([resource_type, path, tenant])
        if code != 0:
            return {
                "resource": resource_type,
                "state": "transform-failed",
                "drops": sorted(drops),
                "unexpected": unexpected,
                "known_holds": known_holds,
                "path": path,
                "code": code,
            }
    return {
        "resource": resource_type,
        "state": state,
        "drops": sorted(drops),
        "unexpected": unexpected,
        "known_holds": known_holds,
        "path": path,
    }


def _format_drop(path, known_holds):
    issue = known_holds.get(path)
    if issue:
        return "%s (%s)" % (path, issue)
    return path


def render_result(result):
    rt = result["resource"]
    state = result["state"]
    if state == "clean":
        return "CLEAN %s" % rt
    if state == "known-hold":
        known_holds = result.get("known_holds") or {}
        return "KNOWN-HOLD %s: %s" % (
            rt,
            ", ".join(_format_drop(path, known_holds) for path in result["drops"]))
    if state == "missing-pull":
        return "FAIL %s: missing %s" % (rt, result["path"])
    if state == "transform-failed":
        return "FAIL %s: transform exited %s" % (rt, result.get("code"))
    return "FAIL %s: unexpected dropped paths: %s" % (
        rt, ", ".join(result["unexpected"]))


def run(tenant, selectors, pulls_dir=None, write=True, status=None):
    validate_tenant(tenant)
    status = status if status is not None else load_status()
    resources = expand_resources(selectors)
    pulls_dir = pulls_dir or os.path.join("pulls", tenant)
    results = []
    for rt in resources:
        results.append(check_resource(rt, tenant, pulls_dir, status, write=write))
    return results


USAGE = (
    "usage: python -m tools.adoption_check <tenant> <resource|product ...>\n"
    "       python -m tools.adoption_check --resources <resource|product ...>\n"
)


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if argv and argv[0] == "--resources":
        try:
            sys.stdout.write("\n".join(expand_resources(argv[1:])) + "\n")
            return 0
        except ValueError as exc:
            sys.stderr.write("error: %s\n" % exc)
            return 2
    if len(argv) < 2:
        sys.stderr.write(USAGE)
        return 2
    tenant = argv[0]
    try:
        results = run(
            tenant, argv[1:],
            write=not bool(os.environ.get("CHECK_ONLY")),
        )
    except (IOError, OSError, KeyError, ValueError) as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 2
    bad = False
    for result in results:
        line = render_result(result)
        sys.stdout.write(line + "\n")
        if result["state"] not in ("clean", "known-hold"):
            bad = True
    if bad:
        sys.stdout.write(
            "adoption-check failed: fix unexpected paths or fetch gaps above\n")
        return 4
    sys.stdout.write("adoption-check passed for %d resource(s)\n" % len(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
