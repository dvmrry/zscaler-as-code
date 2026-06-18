"""Summarize automate.zscaler.com contract artifacts for this repo.

The automate contract reconciler lives outside this template and emits
per-product ``*-divergences.json`` files plus a cross-surface ``rosetta.json``
field matrix. This tool deliberately does not know where that repo is.
Operators pass an exported JSON file in, and we turn it into an advisory fact
sheet for the resource types this repo manages:

* contract-vs-Terraform presence gaps,
* required/readonly/enum drift,
* direct overlap with current override drops.

It is a fact extractor, not a build dependency and not an auto-ack writer.
Expanded GET decorations can be real zac drops without appearing in a POST
contract report, so "not directly in report" is a review note, not a failure.

Stdlib-only, Python 3.6-floor; see AGENTS.md rule 5.
"""

import fnmatch
import json
import os
import sys

from tools.registry import load_registry
from tools.transform import snake as _transform_snake


def _snake(name):
    """Convert automate/SDK field names to zac's snake_case field spelling."""
    if not name:
        return name
    return _transform_snake(name)


def _snake_list(values):
    return sorted(_snake(v) for v in (values or []))


def _product_prefix(product):
    return (product or "").replace("-", "_")


def _variants(name):
    out = [name]
    if not name.endswith("s"):
        out.append(name + "s")
    if name.endswith("y"):
        out.append(name[:-1] + "ies")
    return out


def _normalize_path(path):
    out = []
    for part in (path or "").split("?")[0].strip("/").split("/"):
        if not part:
            continue
        if part.startswith(":") or (part.startswith("{") and part.endswith("}")):
            out.append("*")
        else:
            out.append(part.lower())
    return "/".join(out)


def _path_matches(report_path, fetch_path):
    report = _normalize_path(report_path)
    fetch = _normalize_path(fetch_path)
    return bool(fetch and (report == fetch or report.endswith("/" + fetch)))


def resource_type_for(product, report_resource, registry=None, path=None):
    prefix = _product_prefix(product)
    base = report_resource
    if not base.startswith(prefix + "_"):
        base = prefix + "_" + base
    if registry is None:
        return base
    managed = set(rt for rt, entry in registry.items()
                  if entry.get("generate"))
    for candidate in _variants(base):
        if candidate in managed:
            return candidate
    if path:
        for rt, entry in sorted(registry.items()):
            if not entry.get("generate") or entry.get("product") != product:
                continue
            fetch_path = (entry.get("fetch") or {}).get("path")
            if _path_matches(path, fetch_path):
                return rt
    return base


def load_report(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or (
            "resources" not in data and "rows" not in data):
        raise ValueError("%s is not an automate contract report" % path)
    if "resources" in data and not isinstance(data.get("resources"), list):
        raise ValueError("%s has a non-list resources field" % path)
    if "rows" in data and not isinstance(data.get("rows"), list):
        raise ValueError("%s has a non-list rows field" % path)
    return data


def load_override(resource_type, overrides_dir):
    path = os.path.join(overrides_dir, resource_type + ".json")
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _selector_matches(resource_type, product, selectors):
    if not selectors:
        return True
    prefix = _product_prefix(product)
    for tok in selectors:
        if tok in (product, prefix):
            return True
        if tok == resource_type:
            return True
        if fnmatch.fnmatch(resource_type, tok):
            return True
    return False


def _managed_report_entries(report, registry, selectors):
    product = report.get("product")
    managed = set(rt for rt, entry in registry.items()
                  if entry.get("generate"))
    entries = []
    for entry in report.get("resources") or []:
        rt = resource_type_for(
            product, entry.get("resource", ""), registry=registry,
            path=entry.get("path"))
        if rt not in managed:
            continue
        if not _selector_matches(rt, product, selectors):
            continue
        entries.append((rt, entry))
    return sorted(entries, key=lambda pair: pair[0])


def _presence(entry, surface, key):
    if surface == "root":
        return (entry.get("presence") or {}).get(key) or []
    return (((entry.get(surface) or {}).get("presence") or {}).get(key) or [])


def candidate_fields(entry):
    """Top-level field names the core contract/TF/Go report puts in play.

    The report also carries Python, Ansible, and MCP surfaces. Those are useful
    ecosystem context, but they are too indirect for override cross-checking in
    this repo: a field can be MCP-only without ever appearing in a fetched zac
    payload. Keep the cross-check tied to the live contract, Terraform, Go SDK,
    type drift, and readonly agreement.
    """
    fields = set()
    for key in ("contract_unmatched_in_tf", "contract_only_vs_go",
                "go_only_vs_contract"):
        fields.update(_snake_list(_presence(entry, "root", key)))
    for drift in entry.get("type_drift") or []:
        fields.add(_snake(drift.get("field")))
    for ro in entry.get("readonly") or []:
        fields.add(_snake(ro.get("field")))
    return set(f for f in fields if f)


def _override_paths(override):
    paths = set(override.get("acknowledged_drops") or [])
    paths.update(override.get("drops") or [])
    paths.update((override.get("drop_if_default") or {}).keys())
    return paths


def _handled_paths(override):
    """Paths already handled by an override, whether by drop or transform.

    The direct-support cross-check is about drops, but the "not currently
    acknowledged" review line should not nag about fields already covered by a
    rename target or value_map key.
    """
    paths = set(_override_paths(override))
    paths.update((override.get("renames") or {}).values())
    paths.update((override.get("value_map") or {}).keys())
    return paths


def _top_level(paths):
    return set(p for p in paths if "." not in p and "[]" not in p)


def _format_required(drift):
    out = []
    for row in drift or []:
        field = _snake(row.get("field"))
        direction = row.get("direction") or "drift"
        out.append("%s: contract required=%s, TF required=%s (%s)" % (
            field, row.get("contract_required"), row.get("tf_required"),
            direction))
    return out


def _format_enum(enum):
    out = []
    for row in (enum or {}).get("value_conflict") or []:
        out.append("%s: contract %s vs TF %s" % (
            _snake(row.get("field")), row.get("contract"), row.get("tf")))
    for row in (enum or {}).get("one_sided") or []:
        out.append("%s: contract %s vs TF %s" % (
            _snake(row.get("field")), row.get("contract"), row.get("tf")))
    return out


def _line_list(lines, indent="  - ", limit=18):
    vals = [v for v in lines if v]
    if not vals:
        return ["  - none"]
    out = [indent + v for v in vals[:limit]]
    if len(vals) > limit:
        out.append(indent + "... %d more" % (len(vals) - limit))
    return out


def _cell(row, surface):
    return ((row.get("cells") or {}).get(surface) or {})


def _present(row, surface):
    return bool(_cell(row, surface).get("present"))


def _markers(row, surface):
    return list(_cell(row, surface).get("markers") or [])


def _description(row):
    text = (row.get("description") or "").strip()
    if not text:
        return ""
    text = " ".join(text.split())
    if len(text) > 180:
        return text[:177].rstrip() + "..."
    return text


def _format_rosetta_row(row):
    field = _snake(row.get("field"))
    desc = _description(row)
    if desc:
        return "%s: %s" % (field, desc)
    return field


def _write_ignored(row):
    desc = (row.get("description") or "").lower()
    return "ignored in put/post/delete" in desc or (
        "only applicable for a get request" in desc)


def _rosetta_resource_rows(report, registry, selectors):
    managed = set(rt for rt, entry in registry.items()
                  if entry.get("generate"))
    grouped = {}
    product_by_rt = {}
    for row in report.get("rows") or []:
        product = row.get("product")
        rt = resource_type_for(
            product, row.get("resource", ""), registry=registry,
            path=row.get("path"))
        if rt not in managed:
            continue
        if not _selector_matches(rt, product, selectors):
            continue
        grouped.setdefault(rt, []).append(row)
        product_by_rt[rt] = product
    return [(rt, product_by_rt[rt], grouped[rt])
            for rt in sorted(grouped)]


def render_rosetta_report(report, registry=None, overrides_dir=None,
                          selectors=None):
    registry = registry if registry is not None else load_registry()
    overrides_dir = overrides_dir or os.path.join("tools", "overrides")
    selectors = selectors or []
    entries = _rosetta_resource_rows(report, registry, selectors)
    lines = [
        "# Contract rosetta facts",
        "",
        "Managed resources in scope: %d" % len(entries),
    ]
    if selectors:
        lines.append("Selector: %s" % " ".join(selectors))
    lines.append("")

    for rt, product, rows in entries:
        override = load_override(rt, overrides_dir)
        known = _top_level(_override_paths(override))
        handled = _top_level(_handled_paths(override))
        contract_no_tf = []
        tf_no_contract = []
        write_ignored = []
        marked = []
        by_field = {}
        method = "?"
        path = "?"
        for row in rows:
            field = _snake(row.get("field"))
            if not field:
                continue
            by_field[field] = row
            method = row.get("method") or method
            path = row.get("path") or path
            if _present(row, "contract") and not _present(row, "tf"):
                contract_no_tf.append(row)
            if _present(row, "tf") and not _present(row, "contract"):
                tf_no_contract.append(row)
            if _write_ignored(row):
                write_ignored.append(row)
            marker_bits = []
            for surface in ("contract", "go", "python", "tf", "ansible", "mcp"):
                for marker in _markers(row, surface):
                    marker_bits.append("%s:%s" % (surface, marker))
            if marker_bits:
                marked.append("%s (%s)" % (field, ", ".join(marker_bits)))

        contract_no_tf_fields = set(_snake(r.get("field"))
                                    for r in contract_no_tf)
        write_ignored_fields = set(_snake(r.get("field"))
                                   for r in write_ignored)
        dropped_contract_no_tf = sorted(known & contract_no_tf_fields)
        dropped_write_ignored = sorted(known & write_ignored_fields)
        not_in_rosetta = sorted(known - set(by_field))
        unhandled = sorted(contract_no_tf_fields - handled
                           - write_ignored_fields)

        lines.extend([
            "## %s" % rt,
            "",
            "%s %s" % (method, path),
            "product: %s" % product,
            "",
            "Contract fields absent from Terraform:",
        ])
        lines.extend(_line_list([_format_rosetta_row(r)
                                 for r in sorted(
                                     contract_no_tf,
                                     key=lambda row: _snake(row.get("field")))]))
        lines.append("Terraform fields absent from contract:")
        lines.extend(_line_list([_format_rosetta_row(r)
                                 for r in sorted(
                                     tf_no_contract,
                                     key=lambda row: _snake(row.get("field")))]))
        lines.append("GET-only / write-ignored contract fields:")
        lines.extend(_line_list([_format_rosetta_row(r)
                                 for r in sorted(
                                     write_ignored,
                                     key=lambda row: _snake(row.get("field")))]))
        lines.append("Rosetta markers:")
        lines.extend(_line_list(sorted(marked)))
        lines.append("Override cross-check:")
        lines.extend(_line_list(
            ["dropped contract fields absent from Terraform: %s" %
             ", ".join(dropped_contract_no_tf)]
            if dropped_contract_no_tf
            else ["dropped contract fields absent from Terraform: none"]))
        lines.extend(_line_list(
            ["dropped GET-only/write-ignored fields: %s" %
             ", ".join(dropped_write_ignored)]
            if dropped_write_ignored
            else ["dropped GET-only/write-ignored fields: none"]))
        lines.extend(_line_list(
            ["dropped fields not in rosetta: %s" % ", ".join(not_in_rosetta)]
            if not_in_rosetta else ["dropped fields not in rosetta: none"]))
        lines.extend(_line_list(
            ["contract-not-TF fields not currently handled: %s" %
             ", ".join(unhandled)]
            if unhandled
            else ["contract-not-TF fields not currently handled: none"]))
        lines.append("")

    if not entries:
        lines.append("No managed resources matched the rosetta/selector.")
        lines.append("")
    return "\n".join(lines)


def render_report(report, registry=None, overrides_dir=None, selectors=None):
    if "rows" in report:
        return render_rosetta_report(
            report, registry=registry, overrides_dir=overrides_dir,
            selectors=selectors)
    registry = registry if registry is not None else load_registry()
    overrides_dir = overrides_dir or os.path.join("tools", "overrides")
    selectors = selectors or []
    product = report.get("product") or "(unknown)"
    entries = _managed_report_entries(report, registry, selectors)
    lines = [
        "# Contract facts: %s" % product,
        "",
        "Managed resources in scope: %d" % len(entries),
    ]
    if selectors:
        lines.append("Selector: %s" % " ".join(selectors))
    lines.append("")

    for rt, entry in entries:
        override = load_override(rt, overrides_dir)
        candidates = candidate_fields(entry)
        known = _override_paths(override)
        handled = _handled_paths(override)
        supported = sorted(_top_level(known) & candidates)
        not_direct = sorted(_top_level(known) - candidates)
        unacked = sorted(candidates - _top_level(handled))
        counts = entry.get("counts") or {}

        lines.extend([
            "## %s" % rt,
            "",
            "%s %s" % (entry.get("method") or "?", entry.get("path") or "?"),
            "counts: contract=%s / terraform=%s / go=%s" % (
                counts.get("contract", "?"), counts.get("tf", "?"),
                counts.get("go", "?")),
            "",
            "Contract fields not in Terraform:",
        ])
        lines.extend(_line_list(_snake_list(
            _presence(entry, "root", "contract_unmatched_in_tf"))))
        lines.append("SDK/provider fields absent from contract:")
        lines.extend(_line_list(_snake_list(
            _presence(entry, "root", "go_only_vs_contract"))))
        lines.append("Required drift:")
        lines.extend(_line_list(_format_required(entry.get("required_drift"))))
        lines.append("Readonly agreement:")
        readonly = ["%s (tf_computed=%s, agree=%s)" % (
            _snake(r.get("field")), r.get("tf_computed"), r.get("agree"))
            for r in (entry.get("readonly") or [])]
        lines.extend(_line_list(readonly))
        lines.append("Enum drift:")
        lines.extend(_line_list(_format_enum(entry.get("enum"))))
        lines.append("Override cross-check:")
        lines.extend(_line_list(
            ["directly supported: %s" % ", ".join(supported)]
            if supported else ["directly supported: none"]))
        lines.extend(_line_list(
            ["not directly in report: %s" % ", ".join(not_direct)]
            if not_direct else ["not directly in report: none"]))
        lines.extend(_line_list(
            ["report candidates not currently acknowledged: %s" %
             ", ".join(unacked)]
            if unacked else ["report candidates not currently acknowledged: none"]))
        lines.append("")
    if not entries:
        lines.append("No managed resources matched the report/selector.")
        lines.append("")
    return "\n".join(lines)


USAGE = (
    "usage: python -m tools.contract_facts <divergences.json|rosetta.json> "
    "[RESOURCE|product ...]\n"
)


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        sys.stderr.write(USAGE)
        return 2
    path = argv[0]
    try:
        report = load_report(path)
        sys.stdout.write(render_report(report, selectors=argv[1:]) + "\n")
        return 0
    except (IOError, OSError, ValueError) as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
