"""Plan-diff policy gates for tenant changes — NEW additions only.

Field-designed checks that run between `plan` and approval, against
`terraform show -json tfplan` output. They deliberately judge only what
THIS plan ADDS (legacy tenant data is whatever it is — config-wide
scans of it are noise; the gate's job is stopping new mistakes):

1. SUBDOMAIN REDUNDANCY — a newly added concrete host that is already
   covered by a wildcard/leading-dot base (".example.com" or
   "*.example.com") anywhere in the categories is redundant: it adds
   nothing, bloats the category, and (worse) creates a more-specific
   entry that can shadow policy. The SSL-exemptions category is exempt
   — adding concrete hosts THERE is exactly what check 2 requires.

2. SSL BYPASS VALIDATION — ZIA is most-specific-match: adding
   "www.example.com" to any category while the SSL-exemptions category
   carries ".example.com" re-categorizes that host, and SSL inspection
   ENGAGES on traffic the bypass was protecting. Every newly added
   host that falls under an exemptions suffix must ALSO be added to
   the exemptions category as an exact entry.

3. LOCATION IP OVERLAP - a newly added location IP/CIDR/range that
   overlaps another location range creates ambiguous location assignment.
   Existing overlap is left alone; new overlap is blocked.

The SSL-exemptions category is tenant-specific, so it is configured,
never hardcoded: set SSL_EXEMPT_CATEGORY to the category's CONFIG KEY
(the items map key). Check 2 is skipped with a loud note when unset.
Baselines (wildcard bases, exemption entries) come from the tenant's
committed config — the same files the plan was built from.

Exit: 0 = clean; 1 = violations (each line names the host, the
covering base, and the fix); 2 = usage. Offline; no credentials.

Stdlib-only, Python 3.6-floor — see AGENTS.md rule 5.
"""
import json
import os
import sys
import ipaddress

URL_FIELDS = ("urls", "db_categorized_urls")


def _hosts(entry_list):
    return [e for e in entry_list or [] if isinstance(e, str) and e]


def _is_base(entry):
    return entry.startswith(".") or entry.startswith("*.")


def _norm_base(entry):
    """'.example.com' / '*.example.com' -> 'example.com'."""
    return entry.lstrip("*").lstrip(".").lower()


def _covered_by(host, base):
    """Concrete host falls under a wildcard base (subdomain or exact)."""
    h = host.lower().rstrip(".")
    return h == base or h.endswith("." + base)


def added_hosts(plan):
    """[(category_key, host)] newly ADDED to url categories by this
    plan (after minus before, per instance; create = everything)."""
    out = []
    for rc in plan.get("resource_changes") or []:
        if rc.get("type") != "zia_url_categories":
            continue
        actions = set((rc.get("change") or {}).get("actions") or [])
        if not actions & {"create", "update"}:
            continue
        key = rc.get("index")
        change = rc.get("change") or {}
        before, after = change.get("before") or {}, change.get("after") or {}
        for field in URL_FIELDS:
            olds = set(_hosts(before.get(field)))
            for entry in _hosts(after.get(field)):
                if entry not in olds:
                    out.append((key, entry))
    return out


def added_location_ranges(plan):
    """[(location_key, range)] newly ADDED to location ip_addresses."""
    out = []
    for rc in plan.get("resource_changes") or []:
        if rc.get("type") != "zia_location_management":
            continue
        actions = set((rc.get("change") or {}).get("actions") or [])
        if not actions & {"create", "update"}:
            continue
        key = rc.get("index")
        change = rc.get("change") or {}
        before, after = change.get("before") or {}, change.get("after") or {}
        olds = set(_hosts(before.get("ip_addresses")))
        for entry in _hosts(after.get("ip_addresses")):
            if entry not in olds:
                out.append((key, entry))
    return out


def load_items(tenant, resource_type):
    path = os.path.join("config", tenant, resource_type + ".auto.tfvars.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("items") or {}


def load_categories(tenant):
    return load_items(tenant, "zia_url_categories")


def load_locations(tenant):
    return load_items(tenant, "zia_location_management")


def check_redundancy(adds, categories, exempt_key):
    """Newly added concrete hosts already covered by a wildcard base."""
    bases = {}  # normalized base -> (category key, raw entry)
    for key in sorted(categories):
        for field in URL_FIELDS:
            for entry in _hosts(categories[key].get(field)):
                if _is_base(entry):
                    bases.setdefault(_norm_base(entry), (key, entry))
    failures = []
    for key, entry in adds:
        if key == exempt_key or _is_base(entry):
            continue
        host = entry.split("/", 1)[0]
        labels = host.lower().split(".")
        for i in range(len(labels)):
            candidate = ".".join(labels[i:])
            hit = bases.get(candidate)
            if hit and _covered_by(host, candidate):
                bkey, braw = hit
                failures.append(
                    "REDUNDANT %s: %r is already covered by %r in "
                    "category %r — adding it changes nothing except "
                    "creating a more-specific entry that can shadow "
                    "policy; drop the addition" % (key, entry, braw, bkey))
                break
    return failures


def check_ssl_bypass(adds, categories, exempt_key):
    """Newly added hosts under an SSL-exemptions suffix must also be
    added to the exemptions category as exact entries."""
    exempt = categories.get(exempt_key) or {}
    suffixes = {}   # normalized base -> raw entry
    exact = set()
    for field in URL_FIELDS:
        for entry in _hosts(exempt.get(field)):
            if _is_base(entry):
                suffixes[_norm_base(entry)] = entry
            else:
                exact.add(entry.lower().rstrip("."))
    failures = []
    for key, entry in adds:
        if key == exempt_key or _is_base(entry):
            continue
        host = entry.split("/", 1)[0].lower().rstrip(".")
        labels = host.split(".")
        matched = None
        for i in range(len(labels)):
            candidate = ".".join(labels[i:])
            if candidate in suffixes and _covered_by(host, candidate):
                matched = suffixes[candidate]
                break
        if matched and host not in exact:
            failures.append(
                "SSL-BYPASS %s: %r falls under SSL-exemptions suffix %r "
                "but is NOT an exact entry there — most-specific-match "
                "re-categorizes it and SSL inspection ENGAGES on bypassed "
                "traffic; add %r to the %r category in the same change"
                % (key, entry, matched, host, exempt_key))
    return failures


def _ip_interval(entry):
    text = entry.strip()
    try:
        if "-" in text:
            parts = [p.strip() for p in text.split("-")]
            if len(parts) != 2:
                raise ValueError("bad range")
            start = ipaddress.ip_address(u"" + parts[0])
            end = ipaddress.ip_address(u"" + parts[1])
            if start.version != end.version:
                raise ValueError("mixed IP versions")
            if int(start) > int(end):
                raise ValueError("range start after end")
            return start.version, int(start), int(end)
        if "/" in text:
            network = ipaddress.ip_network(u"" + text, strict=False)
            return (
                network.version,
                int(network.network_address),
                int(network.broadcast_address),
            )
        ip = ipaddress.ip_address(u"" + text)
        return ip.version, int(ip), int(ip)
    except ValueError:
        return None


def _overlaps(left, right):
    return (
        left[0] == right[0]
        and left[1] <= right[2]
        and right[1] <= left[2]
    )


def check_location_ip_overlap(adds, locations):
    """New location ranges must not overlap existing or same-plan ranges."""
    seen = []
    failures = []
    for key in sorted(locations):
        for entry in _hosts(locations[key].get("ip_addresses")):
            interval = _ip_interval(entry)
            if interval is not None:
                seen.append((interval, key, entry))
    for key, entry in adds:
        interval = _ip_interval(entry)
        if interval is None:
            failures.append(
                "LOCATION-IP %s: %r is not an IP / CIDR / address range "
                "- fix the value before approval" % (key, entry))
            continue
        for other, other_key, other_entry in seen:
            if _overlaps(interval, other):
                failures.append(
                    "LOCATION-IP-OVERLAP %s: %r overlaps %r in location "
                    "%r - location assignment becomes ambiguous; narrow "
                    "or remove one range" % (key, entry, other_entry, other_key))
                break
        seen.append((interval, key, entry))
    return failures


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 2:
        sys.stderr.write(
            "usage: terraform show -json tfplan > plan.json && "
            "python -m tools.plan_checks plan.json <tenant>   "
            "(make plan-checks; SSL_EXEMPT_CATEGORY=<config key> enables "
            "the bypass check)\n")
        return 2
    plan_path, tenant = argv
    try:
        with open(plan_path, encoding="utf-8") as f:
            plan = json.load(f)
        if not isinstance(plan, dict) or "format_version" not in plan:
            raise ValueError("not plan JSON (no format_version) — feed "
                             "this `terraform show -json tfplan` output")
        categories = load_categories(tenant)
        locations = load_locations(tenant)
    except (ValueError, OSError) as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 1
    exempt_key = os.environ.get("SSL_EXEMPT_CATEGORY") or None

    adds = added_hosts(plan)
    failures = check_redundancy(adds, categories, exempt_key)
    location_adds = added_location_ranges(plan)
    failures += check_location_ip_overlap(location_adds, locations)
    if exempt_key:
        if exempt_key not in categories:
            sys.stderr.write("error: SSL_EXEMPT_CATEGORY %r is not a key in "
                             "config/%s/zia_url_categories.auto.tfvars.json\n"
                             % (exempt_key, tenant))
            return 1
        failures += check_ssl_bypass(adds, categories, exempt_key)
    else:
        sys.stdout.write("note: SSL_EXEMPT_CATEGORY unset — the SSL-bypass "
                         "check is OFF (set it to the exemptions category's "
                         "config key to enable)\n")
    for line in failures:
        sys.stdout.write(line + "\n")
    checked = len(adds) + len(location_adds)
    sys.stdout.write(
        "%d newly added URL/location entr%s checked, %d violation(s)\n"
        % (checked, "y" if checked == 1 else "ies", len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
