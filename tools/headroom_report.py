"""Report provider surface vs committed adoption status.

This is the repeatable version of the DAV-25 scratch reasoning: compare
every resource in the committed Terraform provider schema dumps with the
registry and the repo-owned adoption status facts.

It does not claim an unregistered provider resource is production-ready.
It says whether the repo currently manages it, whether it has a recorded
non-config disposition, or whether it is still module-ready headroom that
needs fetch/import/live classification.

Stdlib-only, Python 3.6-floor; see AGENTS.md rule 5.
"""
import fnmatch
import sys

from tools.adoption_status import known_holds_for, load_status
from tools.registry import load_registry
from tools.tfschema import PROVIDER_PREFIXES, load_provider


def provider_resources():
    out = {}
    for provider in sorted(set(PROVIDER_PREFIXES.values())):
        data = load_provider(provider)
        for rt in sorted(data.get("resource_schemas") or {}):
            out[rt] = provider
    return out


def _selector_matches(resource_type, product, selectors):
    if not selectors:
        return True
    for tok in selectors:
        if tok == product:
            return True
        if tok == resource_type:
            return True
        if fnmatch.fnmatch(resource_type, tok):
            return True
    return False


def _hold_note(holds):
    if not holds:
        return ""
    bits = []
    for hold in holds:
        issue = hold.get("issue")
        path = hold.get("path")
        bits.append("%s%s" % (path, " (%s)" % issue if issue else ""))
    return "known hold: " + ", ".join(bits)


def classify_resource(resource_type, product, registry, status):
    entry = registry.get(resource_type)
    if entry and entry.get("generate"):
        if entry.get("derive"):
            state = "managed-derived"
            note = "derived from %s" % entry["derive"].get("from")
        elif entry.get("fetch"):
            state = "managed-fetch"
            note = "fetch %s/%s" % (
                entry["fetch"].get("pagination", product),
                entry["fetch"].get("path"),
            )
        else:
            state = "managed-no-fetch"
            note = "generated but has no fetch/derive entry"
    else:
        disposition = (status.get("dispositions") or {}).get(resource_type)
        if disposition:
            state = disposition["status"]
            note = disposition.get("reason", "")
        else:
            state = "module-ready"
            note = "provider resource not adopted; classify fetch/import/live behavior"
    holds = _hold_note(known_holds_for(resource_type, status))
    if holds:
        note = "%s; %s" % (note, holds) if note else holds
    return state, note


def summarize(rows):
    counts = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return counts


def render_report(registry=None, status=None, selectors=None):
    registry = registry if registry is not None else load_registry()
    status = status if status is not None else load_status()
    selectors = selectors or []
    resources = provider_resources()
    rows = []
    for rt in sorted(resources):
        product = resources[rt]
        if not _selector_matches(rt, product, selectors):
            continue
        state, note = classify_resource(rt, product, registry, status)
        rows.append({
            "resource": rt,
            "product": product,
            "status": state,
            "note": note,
        })
    counts = summarize(rows)
    lines = [
        "# Headroom report",
        "",
        "Provider resources in scope: %d" % len(rows),
    ]
    if selectors:
        lines.append("Selector: %s" % " ".join(selectors))
    lines.extend([
        "",
        "## Summary",
        "",
    ])
    for status_name in sorted(counts):
        lines.append("- %s: %d" % (status_name, counts[status_name]))
    if not counts:
        lines.append("- none")
    lines.extend([
        "",
        "## Resources",
        "",
        "| resource | product | status | note |",
        "|---|---|---|---|",
    ])
    for row in rows:
        lines.append("| `%s` | `%s` | `%s` | %s |" % (
            row["resource"], row["product"], row["status"],
            row["note"].replace("|", "\\|"),
        ))
    if not rows:
        lines.append("| _none_ |  |  |  |")
    lines.append("")
    return "\n".join(lines)


USAGE = "usage: python -m tools.headroom_report [RESOURCE|product|glob ...]\n"


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    try:
        sys.stdout.write(render_report(selectors=argv) + "\n")
        return 0
    except (IOError, OSError, ValueError) as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
