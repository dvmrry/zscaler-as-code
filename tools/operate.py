"""Apply ONE safe, additive edit to a tenant's committed config — the BAU
churn operations (a URL on a category, a domain on an app segment) as a
deterministic, idempotent, reviewable change.

This is deliberately NOT free-form JSON editing. Only an allowlisted
(resource_type, field) — a list of plain strings where order doesn't matter —
can be touched, so a fat-finger can't reshape a structured field (paired
tcp_port_ranges, {id} reference objects, nested blocks). The edit is
config-only: it produces a reviewable diff that the normal
plan -> approval -> apply path enforces. It never calls the tenant API.

`resolve` maps a ticket's DISPLAY name ("Blocked Social") to the config key the
edit needs — so the fuzzy front end of docs/workflows/operate-change.md grounds
on committed config instead of guessing a key.

Stdlib-only, Python 3.6-floor — see AGENTS.md rule 5.
"""
import json
import os
import re
import sys

from tools.transform import render_tfvars

CONFIG_SUFFIX = ".auto.tfvars.json"

# (resource_type, field) pairs safe to add/remove a string element on: each is
# a list of plain strings, order-insensitive, canonically sorted on write. The
# set is intentionally minimal — exactly the high-churn fields that have BOTH a
# make target and a test, so the workflow never claims a path it can't run.
# Widening it (e.g. url-category keywords / db_categorized_urls) is a deliberate
# add: one entry here + a thin target + a test. Structured fields (paired
# tcp_port_ranges, {id} refs, nested blocks) are refused.
EDITABLE = {
    ("zia_url_categories", "urls"),
    ("zpa_application_segment", "domain_names"),
}

# Tenant becomes a path component (config/<tenant>/...); validate it the same way
# fetch.py does so neither the make target nor a direct CLI call can traverse
# (`..`, `../x`). resource_type is bounded by the EDITABLE/NAME_FIELD allowlists.
_VALID_TENANT = re.compile(r"^[A-Za-z0-9_.-]+$")


def _check_tenant(tenant):
    if not _VALID_TENANT.match(tenant or "") or tenant in (".", ".."):
        raise ValueError("tenant must match [A-Za-z0-9_.-]+ and not be . or .. "
                         "(got %r)" % tenant)

# The human-facing name field per resource type, for resolve().
NAME_FIELD = {
    "zia_url_categories": "configured_name",
    "zpa_application_segment": "name",
}


def _config_path(config_root, tenant, resource_type):
    return os.path.join(config_root, tenant, resource_type + CONFIG_SUFFIX)


def _load_items(path):
    if not os.path.isfile(path):
        raise ValueError("no config at %s (wrong tenant or resource type?)" % path)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("items") or {}


def _canonical(values):
    """Dedup + canonical sort — mirrors transform's set canonicalization so an
    edit leaves the file byte-identical to a fresh fetch + transform."""
    out = []
    for v in values:
        if v not in out:
            out.append(v)
    return sorted(out, key=lambda v: "" if v is None else str(v))


def resolve(tenant, resource_type, display_name, config_root="config"):
    """[(config_key, display_name)] whose NAME_FIELD matches display_name
    (case-insensitive substring). Zero or many is a signal to the caller to
    clarify — never to guess."""
    _check_tenant(tenant)
    field = NAME_FIELD.get(resource_type)
    if field is None:
        raise ValueError("no name field known for %s" % resource_type)
    items = _load_items(_config_path(config_root, tenant, resource_type))
    needle = display_name.strip().lower()
    hits = []
    for key in sorted(items):
        name = items[key].get(field)
        if isinstance(name, str) and needle in name.lower():
            hits.append((key, name))
    return hits


def operate(op, tenant, resource_type, key, field, value, config_root="config"):
    """add|remove `value` to/from items[key][field]. Returns a one-line status.
    Raises ValueError on a refusal (unsafe field, missing file/item); nothing is
    written on a refusal or a no-op."""
    if op not in ("add", "remove"):
        raise ValueError("op must be add or remove, got %r" % op)
    _check_tenant(tenant)
    if (resource_type, field) not in EDITABLE:
        raise ValueError(
            "refusing to edit %s.%s — not an allowlisted list-of-strings "
            "field. Editable: %s" % (resource_type, field,
                ", ".join("%s.%s" % p for p in sorted(EDITABLE))))
    path = _config_path(config_root, tenant, resource_type)
    items = _load_items(path)
    if key not in items:
        cand = ", ".join(sorted(items)[:8]) or "(none)"
        raise ValueError(
            "%r is not a config key in %s (this is the key, not the display "
            "name — use `resolve` first). Candidates: %s"
            % (key, resource_type, cand))
    cur = items[key].get(field) or []
    if not isinstance(cur, list):
        raise ValueError("%s.%s is not a list in %s" % (key, field, path))
    present = value in cur
    if op == "add" and present:
        return "no-op: %s already in %s.%s" % (value, key, field)
    if op == "remove" and not present:
        return "no-op: %s not in %s.%s" % (value, key, field)
    if op == "add":
        items[key][field] = _canonical(cur + [value])
    else:
        items[key][field] = _canonical([v for v in cur if v != value])
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_tfvars(items))
    return "%s %s %s %s.%s (now %d entries)" % (
        op, value, "to" if op == "add" else "from",
        key, field, len(items[key][field]))


_USAGE = (
    "usage:\n"
    "  python -m tools.operate <add|remove> <tenant> <resource_type> <key> "
    "<field> <value>\n"
    "  python -m tools.operate resolve <tenant> <resource_type> <display_name>\n"
)


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if argv and argv[0] == "resolve":
        if len(argv) != 4:
            sys.stderr.write(_USAGE)
            return 2
        try:
            hits = resolve(argv[1], argv[2], argv[3])
        except ValueError as exc:
            sys.stderr.write("error: %s\n" % exc)
            return 1
        for key, name in hits:
            sys.stdout.write("%s\t%s\n" % (key, name))
        if not hits:
            sys.stderr.write("no match for %r in %s\n" % (argv[3], argv[2]))
        return 0
    if len(argv) != 6:
        sys.stderr.write(_USAGE)
        return 2
    op, tenant, resource_type, key, field, value = argv
    try:
        msg = operate(op, tenant, resource_type, key, field, value)
    except ValueError as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 1
    sys.stdout.write(msg + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
