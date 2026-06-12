"""Semantic lint for committed config — misconfig the schema can't see.

Type-correct config can still be operationally wrong: pasted invisible
characters, URL entries with schemes, duplicate list entries the API
will silently dedupe (perma-drift), colliding rule orders, values a
provider runtime validator rejects, and — the quiet killer — a URL
added to one category that is MORE SPECIFIC than an entry in another
category, silently pulling that traffic out of whatever policies
(e.g. SSL bypass) matched the broader entry (ZIA most-specific-match).

ERRORs gate (exit 1); WARNs report. Every line carries a suggested fix
— like typecheck, the output is the decision table. All checks are
deterministic and offline: no DNS, no API.

Stdlib-only, Python 3.6-floor — see AGENTS.md rule 5.
"""
import json
import os
import re
import sys

from tools.registry import generated_types
from tools.tfschema import load_resource
from tools.transform import load_override, render_tfvars

# Fields holding host-shaped entries (domains / URLs with paths). zpa
# domain_names is included for the case check: the ZPA API lowercases
# domain names on response (provider troubleshooting guide), so an
# uppercase hand-edit perma-diffs.
URL_ENTRY_FIELDS = ("urls", "db_categorized_urls", "domain_names")
# Fields holding IPs / CIDRs / dash ranges.
IP_FIELDS = ("ip_ranges", "ip_ranges_retaining_parent_category", "ip_addresses")
# Resources whose `order` must be unique — grouped by a field when listed.
ORDER_GROUP_FIELD = {"zia_cloud_app_control_rule": "type"}

_HTML_ENTITY = re.compile(r"&(?:[A-Za-z]+|#[0-9]+|#x[0-9A-Fa-f]+);")
_INVISIBLE = ("​", "‌", "‍", "⁠", "﻿")
_PASTED = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", " ": " ",
}


class Report(object):
    def __init__(self):
        self.errors = []
        self.warnings = []

    def error(self, rt, where, problem, fix):
        self.errors.append("ERROR %s: %s: %s — %s" % (rt, where, problem, fix))

    def warn(self, rt, where, problem, fix):
        self.warnings.append("WARN  %s: %s: %s — %s" % (rt, where, problem, fix))


# ---------------------------------------------------------------------------
# String hygiene (every string value, recursively)
# ---------------------------------------------------------------------------

def check_string(value, rt, where, report, allow_entities=False):
    for ch in value:
        if ch in _INVISIBLE:
            report.error(rt, where, "invisible character U+%04X" % ord(ch),
                         "delete it (pasted zero-width/BOM)")
            break
    for ch in value:
        if (ord(ch) < 32 and ch not in "\n\t") or ord(ch) == 127:
            report.error(rt, where, "control character U+%04X" % ord(ch),
                         "remove it")
            break
    if value != value.strip():
        report.error(rt, where, "leading/trailing whitespace %r" % value,
                     "strip it (the API strips too -> perma-drift)")
    for ch, plain in sorted(_PASTED.items()):
        if ch in value:
            report.warn(rt, where, "pasted punctuation U+%04X" % ord(ch),
                        "replace with plain %r if unintended" % plain)
            break
    if "  " in value.strip():
        report.warn(rt, where, "doubled internal spaces",
                    "collapse to one if unintended")
    m = _HTML_ENTITY.search(value)
    if m and not allow_entities:
        report.warn(rt, where, "HTML entity %r in value" % m.group(0),
                    "use the literal character (ZPA/ZCC reads unescape "
                    "name/description -> perma-diff)")


def walk_strings(value, rt, where, report, allow_entities=False):
    if isinstance(value, str):
        check_string(value, rt, where, report, allow_entities)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            walk_strings(v, rt, "%s[%d]" % (where, i), report,
                         allow_entities)
    elif isinstance(value, dict):
        for k in sorted(value):
            walk_strings(value[k], rt, "%s.%s" % (where, k), report,
                         allow_entities)


# ---------------------------------------------------------------------------
# Field-specific syntax
# ---------------------------------------------------------------------------

def check_url_entry(entry, rt, where, report):
    low = entry.lower()
    if low.startswith("http://") or low.startswith("https://"):
        report.error(rt, where, "URL entry carries a scheme: %r" % entry,
                     "remove the scheme; category entries are host[/path]")
    if any(c in entry for c in (" ", ",", ";", "|", '"')):
        report.error(rt, where, "separator character inside entry %r" % entry,
                     "one entry per list element (CSV paste?)")
    if ".." in entry:
        report.error(rt, where, "consecutive dots in %r" % entry, "fix the typo")
    if entry != low:
        report.warn(rt, where, "uppercase in %r" % entry,
                    "hosts are case-insensitive; lowercase for stable diffs")


def check_ip_entry(entry, rt, where, report):
    import ipaddress

    def _ok(text):
        try:
            text = text.strip()
            if "/" in text:
                ipaddress.ip_network(u"" + text, strict=False)
            else:
                ipaddress.ip_address(u"" + text)
            return True
        except ValueError:
            return False

    parts = entry.split("-")
    if len(parts) == 2 and all(_ok(p) for p in parts):
        return
    if not _ok(entry):
        report.error(rt, where, "not an IP / CIDR / address range: %r" % entry,
                     "fix the value (e.g. 10.0.0.0/24 or 10.0.0.1-10.0.0.9)")


def check_list_duplicates(values, rt, where, report, is_set, is_url_field):
    """Duplicates in SET-typed fields are errors — terraform itself dedupes
    sets, so the config can never round-trip (perma-drift). LIST-typed
    fields keep order and repeats (e.g. ZPA *_port_ranges are PAIRWISE:
    ['9002','9002'] is the range 9002-9002), so only URL-entry lists get
    a semantic warning there."""
    seen = set()
    for v in values:
        try:
            dup = v in seen
            seen.add(v)
        except TypeError:
            continue  # unhashable (dict block members) — not set-typed data
        if dup:
            if is_set:
                report.error(rt, where, "duplicate entry %r in a set" % v,
                             "remove it (terraform dedupes sets -> perma-drift)")
            elif is_url_field:
                report.warn(rt, where, "duplicate entry %r" % v,
                            "remove it if unintended")


def check_ranges(item, key, rt, override, report):
    for field, bounds in sorted((override.get("ranges") or {}).items()):
        value = item.get(field)
        if isinstance(value, str):
            # some range-validated fields are schema STRINGS holding
            # numbers (zpa latitude/longitude) — parse, else skip
            try:
                value = float(value)
            except ValueError:
                continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if not (bounds[0] <= value <= bounds[1]):
            report.error(
                rt, "items.%s.%s" % (key, field),
                "%r outside provider-validated range %s-%s" % (
                    value, bounds[0], bounds[1]),
                "fix the value or drop the field (0/unset means no quota)",
            )


# ---------------------------------------------------------------------------
# Cross-item checks
# ---------------------------------------------------------------------------

def check_dropped_fields(items, rt, override, report):
    """Fields named in the override's `drops` can never round-trip — the
    transform strips them from fetched data because the provider's read
    rewrites or never returns them (operand display names, rhs_list,
    computed orders). They are still IN the schema, so a hand-edit that
    re-adds one passes typecheck and then perma-diffs; gate it here.
    Dotted entries reach inside nested blocks, mirroring the transform."""

    def walk(value, segs, where):
        if isinstance(value, dict):
            head = segs[0]
            if head in value:
                if len(segs) == 1:
                    report.error(
                        rt, "%s.%s" % (where, head),
                        "field %r is transform-dropped (the provider "
                        "rewrites or never returns it — it cannot "
                        "round-trip)" % head,
                        "remove it; see drops in tools/overrides/%s.json"
                        % rt,
                    )
                else:
                    walk(value[head], segs[1:], "%s.%s" % (where, head))
        elif isinstance(value, list):
            for i, v in enumerate(value):
                walk(v, segs, "%s[%d]" % (where, i))

    for path in sorted(override.get("drops") or []):
        segs = path.split(".")
        for key in sorted(items):
            walk(items[key], segs, "items.%s" % key)


def check_order_collisions(items, rt, report):
    group_field = ORDER_GROUP_FIELD.get(rt)
    seen = {}
    for key in sorted(items):
        order = items[key].get("order")
        if order is None:
            continue
        group = items[key].get(group_field) if group_field else None
        slot = (group, order)
        if slot in seen:
            report.error(
                rt, "items.%s.order" % key,
                "order %r collides with %r" % (order, seen[slot]),
                "orders must be unique%s; renumber one"
                % (" per %s" % group_field if group_field else ""),
            )
        else:
            seen[slot] = key


def _split_entry(entry):
    """Normalize a category entry -> (host, path). Lowercase, leading dot
    stripped, path without trailing slash ('' when none)."""
    e = entry.lower().lstrip(".")
    if "/" in e:
        host, path = e.split("/", 1)
        return host, path.rstrip("/")
    return e, ""


def _shadows(specific, broad):
    """True when `specific` is a strictly more specific match than `broad`
    under ZIA most-specific-match (subdomain of a domain entry, or a
    deeper path on the same host)."""
    sh, sp = specific
    bh, bp = broad
    if sh == bh and bh != "":
        if bp == "" and sp != "":
            return True
        return bp != "" and sp.startswith(bp + "/")
    if sh.endswith("." + bh) and bp == "":
        return True
    return False


def check_category_shadowing(items, rt, report):
    """Cross-category specificity conflicts in zia_url_categories.

    An entry in category B that is MORE SPECIFIC than an entry in
    category A re-categorizes that traffic as B — policies matching A
    (SSL bypass lists, filtering rules, ...) silently stop applying to
    it. Identical entries in two categories are fine (multi-membership
    is legal); only specificity differences shadow.

    Scaled for real tenants (field-hit: a single category carried 7500+
    entries and the naive pairwise scan took minutes and emitted
    thousands of lines): each entry checks only its ANCESTORS via an
    index — O(n * depth) — and findings are AGGREGATED to one warning
    per (specific category, broad category) pair with a count and an
    example. LINT_SHADOWING_VERBOSE=1 restores the per-pair lines.
    """
    index = {}
    entries = []
    for key in sorted(items):
        for field in URL_ENTRY_FIELDS:
            for raw in items[key].get(field) or []:
                if isinstance(raw, str) and raw:
                    host, path = _split_entry(raw)
                    entries.append((host, path, raw, key, field))
                    index.setdefault((host, path), {})[key] = raw

    def ancestors(host, path):
        # mirrors _shadows: path prefixes on the same host (and the bare
        # host), then every proper dot-suffix of the host
        if path:
            segs = path.split("/")
            for i in range(len(segs) - 1, 0, -1):
                yield host, "/".join(segs[:i])
            yield host, ""
        labels = host.split(".")
        for i in range(1, len(labels)):
            yield ".".join(labels[i:]), ""

    verbose = bool(os.environ.get("LINT_SHADOWING_VERBOSE"))
    pairs = {}  # (specific_cat, broad_cat) -> [count, example tuple]
    for host, path, raw, key, field in entries:
        for anc in ancestors(host, path):
            for bkey, braw in (index.get(anc) or {}).items():
                if bkey == key:
                    continue
                slot = pairs.setdefault((key, bkey), [0, None])
                slot[0] += 1
                if slot[1] is None:
                    slot[1] = (raw, braw, field)
                if verbose:
                    report.warn(
                        rt, "items.%s.%s" % (key, field),
                        "%r is more specific than %r in category %r — "
                        "policies matching %r via %r will NOT apply to "
                        "this traffic" % (raw, braw, bkey, bkey, braw),
                        "if those policies should still cover it, add %r "
                        "to %r as well" % (raw, bkey),
                    )
    if verbose:
        return
    for (skey, bkey) in sorted(pairs):
        count, (sraw, braw, sfield) = pairs[(skey, bkey)]
        report.warn(
            rt, "items.%s.%s" % (skey, sfield),
            "%d entr%s more specific than entries in category %r (e.g. %r "
            "is more specific than %r) — policies matching %r via those "
            "broader entries will NOT apply to this traffic"
            % (count, "y is" if count == 1 else "ies are", bkey, sraw,
               braw, bkey),
            "if those policies should still cover them, add the specific "
            "entries to %r as well (per-entry list: LINT_SHADOWING_VERBOSE=1)"
            % bkey,
        )


# ---------------------------------------------------------------------------
# Per-file driver
# ---------------------------------------------------------------------------

def lint_config_file(rt, path, report):
    with open(path, "rb") as f:
        raw_bytes = f.read()
    data = json.loads(raw_bytes.decode("utf-8-sig"))
    items = data.get("items") or {}

    # A leading UTF-8 BOM is invisible to the canonical compare below
    # (utf-8-sig strips it on decode), yet it can break terraform's JSON
    # parser — flag it explicitly with its own remediation.
    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        report.error(rt, path, "leading UTF-8 BOM",
                     "run make fmt-config TENANT=<label>")

    canonical = render_tfvars(items)
    if raw_bytes.decode("utf-8-sig") != canonical:
        report.error(
            rt, path, "file is not in canonical transform form "
            "(hand edit / CRLF / unsorted keys)",
            "run make fmt-config TENANT=<label>",
        )

    override = load_override(rt)
    attrs = load_resource(rt)["block"].get("attributes") or {}
    for key in sorted(items):
        item = items[key]
        where = "items.%s" % key
        # no_html_unescape resources legitimately carry entities in
        # config (their reads keep escaped state) — don't warn there
        walk_strings(item, rt, where, report,
                     allow_entities=bool(override.get("no_html_unescape")))
        check_ranges(item, key, rt, override, report)
        for field in sorted(item):
            value = item[field]
            if isinstance(value, list):
                enc = attrs.get(field, {}).get("type")
                is_set = isinstance(enc, list) and enc and enc[0] == "set"
                check_list_duplicates(
                    value, rt, "%s.%s" % (where, field), report,
                    is_set, field in URL_ENTRY_FIELDS)
            if field in URL_ENTRY_FIELDS and isinstance(value, list):
                for i, entry in enumerate(value):
                    if isinstance(entry, str):
                        check_url_entry(
                            entry, rt, "%s.%s[%d]" % (where, field, i), report)
            if field in IP_FIELDS and isinstance(value, list):
                for i, entry in enumerate(value):
                    if isinstance(entry, str):
                        check_ip_entry(
                            entry, rt, "%s.%s[%d]" % (where, field, i), report)

    check_dropped_fields(items, rt, override, report)
    check_order_collisions(items, rt, report)
    if rt == "zia_url_categories":
        check_category_shadowing(items, rt, report)


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        sys.stderr.write("usage: python -m tools.lint <tenant>\n")
        return 2
    tenant = argv[0]
    config_dir = os.path.join("config", tenant)

    report = Report()
    checked = 0
    for rt in generated_types():
        path = os.path.join(config_dir, rt + ".auto.tfvars.json")
        if not os.path.exists(path):
            continue
        checked += 1
        try:
            lint_config_file(rt, path, report)
        except Exception as exc:
            report.error(rt, path, "unreadable: %s" % exc,
                         "fix the file or re-run make transform")

    if not checked:
        sys.stdout.write(
            "error: no config files found for tenant %r in %s "
            "(typo? run make transform first)\n" % (tenant, config_dir))
        return 1

    for line in report.errors + report.warnings:
        sys.stdout.write(line + "\n")
    sys.stdout.write(
        "\n%d error(s), %d warning(s) across %d config file(s)\n"
        % (len(report.errors), len(report.warnings), checked))
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
