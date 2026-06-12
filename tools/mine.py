"""Mine pinned provider Go source for behavioral quirks — the detection
pattern, mechanized.

Every production incident of the first adoption traced to a rule that
already existed in the provider's source: ValidateFuncs (ranges/enums),
flatten shapes (singleton-merge, id-zero stubs, literal defaults, unit
arithmetic, inverted booleans, prefix stripping), DiffSuppressFuncs.
This tool fetches the EXACT pinned provider tags (the same pins the
generator uses), runs the proven pattern battery over each resource's
source (plus the shared helper files), maps each hit to the override
key that encodes it, and reports coverage typecheck-style: one line per
finding, MISSING lines carry the suggested override entry.

Run it after every provider bump (RUNBOOK: Provider Bumps) and when
adding a resource. Heuristics are nearest-preceding-field; treat output
as a worklist to verify against source, not gospel — tools/MINING.md
is the full methodology including the non-mechanical lanes (Ansible
argspecs, API semantics).

Exit: 0 = everything found is covered; 4 = MISSING coverage (the tool's
exit; make flattens to 2 — a red run is the notification); 1 = fetch/
parse failure. Network via HTTPS_PROXY / REQUESTS_CA_BUNDLE like fetch.

Stdlib-only, Python 3.6-floor — see AGENTS.md rule 5.
"""
import json
import os
import re
import ssl
import sys

from tools.gen_module import _load_provider_pins
from tools.registry import generated_types
from tools.tfschema import block_is_single, load_resource
from tools.transform import load_override

REPO = "https://raw.githubusercontent.com/zscaler/terraform-provider-%s/v%s/%s"

# Per-product source layout. zia/zpa are SDKv2 (<product>/resource_<rt>.go);
# zcc is the terraform PLUGIN FRAMEWORK (internal/framework/resources/
# <name-without-prefix>.go) with different validator idioms — the battery
# carries both dialects. {rt} = resource type, {short} = without product
# prefix. Aliases capture files that break the naming convention.
LAYOUTS = {
    "zia": {"paths": ["zia/resource_{rt}.go", "zia/resource_{rt}s.go"],
            "shared": ["zia/common.go", "zia/utils.go", "zia/validator.go"]},
    "zpa": {"paths": ["zpa/resource_{rt}.go", "zpa/resource_{rt}s.go"],
            "shared": ["zpa/common.go"]},
    "zcc": {"paths": ["internal/framework/resources/{short}.go"],
            "shared": ["internal/framework/common/common.go",
                        "internal/framework/helpers/helpers.go"]},
}
FILE_ALIASES = {
    "zpa_application_server": "zpa/resource_zpa_app_server_controller.go",
}

# Matches both dialects' field declarations: SDKv2 `"field": {` and
# plugin-framework `"field": schema.Int64Attribute{`.
_FIELD_RE = re.compile(r'"(\w+)":\s*(?:[\w.\[\]]+)?\{')

PATTERNS = (
    # SDKv2 idioms (zia/zpa)
    ("range_validator", re.compile(
        r'validation\.IntBetween\((-?\d+),\s*(-?\d+)\)'), "ranges"),
    ("range_validator", re.compile(
        r'validation\.Float(?:Between|AtLeast|AtMost)\(([^)]*)\)'), "ranges"),
    ("enum_validator", re.compile(
        r'validation\.StringInSlice\(\[\]string\{([^}]*)\}'), "(enum lint — backlog)"),
    ("diff_suppress", re.compile(r'DiffSuppressFunc:\s*(\w+)'), "(informational)"),
    # plugin-framework idioms (zcc)
    ("range_validator", re.compile(
        r'int64validator\.Between\((-?\d+),\s*(-?\d+)\)'), "ranges"),
    ("enum_validator", re.compile(
        r'stringvalidator\.OneOf\(([^)]*)\)'), "(enum lint — backlog)"),
)


def _fetch(url):
    import urllib.request

    bundle = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    ctx = ssl.create_default_context(cafile=bundle) if bundle else None
    with urllib.request.urlopen(url, context=ctx, timeout=30) as resp:
        return resp.read().decode("utf-8", "replace")


def nearest_field(text, pos):
    """The schema field name declared nearest BEFORE pos (heuristic)."""
    last = None
    for m in _FIELD_RE.finditer(text, 0, pos):
        last = m.group(1)
    return last or "?"


def find_merge_flatten_helpers(shared_text):
    """Helper functions that take a SLICE and ALWAYS return a singleton
    block list (the schema-lies-flatten-merges shape):
    func flattenX(xs []T) []interface{} { ... return []interface{}{map{...}} }.

    The slice parameter is load-bearing: a helper over a single struct/
    pointer (flattenCustomIDSet, flattenCommonZPNERIDSimple) also returns
    one block, but one-in-one-out is not a merge — only N-elements-in,
    one-block-out forces a merge_blocks override."""
    helpers = set()
    for m in re.finditer(
            r'func (flatten\w+)\(([^)]*)\)\s*\[\]interface\{\}\s*\{(.*?)\n\}',
            shared_text, re.DOTALL):
        name, params, body = m.group(1), m.group(2), m.group(3)
        if "[]" not in params:
            continue
        if re.search(r'return \[\]interface\{\}\{\s*\n?\s*map\[string\]interface\{\}\{', body):
            helpers.add(name)
    return helpers


def scan_resource(rt, source, shared, override, findings, block_types=None):
    merge_helpers = find_merge_flatten_helpers(shared + source)

    for klass, pattern, enc in PATTERNS:
        for m in pattern.finditer(source):
            field = nearest_field(source, m.start())
            detail = m.group(0)[:70]
            covered = "MISSING"
            if enc == "ranges" and field in (override.get("ranges") or {}):
                covered = "covered"
            if enc.startswith("("):
                covered = "info"
            findings.append((rt, field, klass, detail, enc, covered))

    # unit conversions: arithmetic on resp fields in the read path
    for m in re.finditer(r'resp\.(\w+)\s*(/|\*)\s*(\d+)', source):
        field_guess = re.sub(r'(?<!^)(?=[A-Z])', '_', m.group(1)).lower()
        covered = ("covered" if field_guess in (override.get("divide") or {})
                   else "MISSING")
        findings.append((rt, field_guess, "unit_conversion", m.group(0)[:70],
                         "divide", covered))

    # literal defaults on empty reads
    for m in re.finditer(
            r'if len\(resp\.(\w+)\) == 0 \{\s*\n\s*_? ?=? ?d\.Set\("(\w+)",\s*(\[\]string\{[^}]*\}|"[^"]*")',
            source):
        field = m.group(2)
        covered = ("covered" if field in (override.get("defaults") or {})
                   else "MISSING")
        findings.append((rt, field, "literal_default", m.group(3)[:70],
                         "defaults", covered))

    # inverted booleans, both dialects and both directions (zcc v0.1.0-beta.1
    # ground truth):
    #   SDKv2 write:     boolToInvertedInt(d.Get("fail_open").(bool))
    #   framework write: payload.X = boolToInvertedStr(plan.Active.ValueBool())
    #   framework read:  plan.Active = invertedStrToBool(p.Active)
    # Helper DEFINITIONS (func boolToInvertedStr(v bool)...) don't match:
    # the argument must be a d.Get, a .ValueBool() chain, or a bare
    # field/selector closed by , or ).
    inverted = set()
    for m in re.finditer(
            r'[Bb]oolToInverted\w*\(\s*(?:d\.Get\("(\w+)"\)'
            r'|(?:\w+\.)*(\w+)\.ValueBool\(\)'
            r'|(?:\w+\.)*(\w+)\s*[,)])', source):
        inverted.add(m.group(1) or m.group(2) or m.group(3))
    for m in re.finditer(r'inverted\w*ToBool\(\s*(?:\w+\.)*(\w+)\s*\)', source):
        inverted.add(m.group(1))
    for raw_name in sorted(inverted):
        field = re.sub(r'(?<!^)(?=[A-Z])', '_', raw_name).lower()
        covered = ("covered" if field in (override.get("invert_bool") or [])
                   else "MISSING")
        findings.append((rt, field, "int_bool_inverted", "boolToInverted*",
                         "invert_bool", covered))

    # prefix strips in the read
    for m in re.finditer(r'strings\.TrimPrefix\([^,]+,\s*"([A-Z_]+_)"\)', source):
        prefix = m.group(1)
        strips = override.get("strip_prefix") or {}
        covered = "covered" if prefix in strips.values() else "MISSING"
        findings.append((rt, nearest_field(source, m.start()), "strip_prefix",
                         prefix, "strip_prefix", covered))

    # merge-flatten helper usage: d.Set("field", <slice-singleton-helper>(...)).
    # Schema-aware: a block the schema already declares single (nesting
    # single or max_items 1) is auto-merged by the transform — covered
    # without an override. merge_blocks is only for blocks where the
    # schema says N but the flatten returns 1 (zpa server_groups).
    for m in re.finditer(r'd\.Set\("(\w+)",\s*(flatten\w+)\(', source):
        field, helper = m.group(1), m.group(2)
        if helper in merge_helpers:
            entry = (block_types or {}).get(field)
            if field in (override.get("merge_blocks") or []):
                covered = "covered"
            elif entry is not None and block_is_single(entry):
                covered = "covered"
                helper += " (schema-single)"
            elif entry is None and block_types is not None:
                covered = "info"  # not a config block; nothing to encode
            else:
                covered = "MISSING"
            findings.append((rt, field, "merge_flatten", helper,
                             "merge_blocks", covered))


def report(findings, fetch_failures, baseline_path, update_baseline, out):
    """Print the worklist, apply the baseline, return the exit code.

    Baseline semantics mirror acknowledged_drops: a signature in
    tools/overrides/mine-baseline.json is a KNOWN finding (triaged, low
    priority, or informational) — still printed, never fatal. Only a
    MISSING finding that is NOT in the baseline exits 4, so a provider
    bump that introduces a new quirk is the only thing that goes red.
    """
    baseline = set()
    if os.path.exists(baseline_path):
        with open(baseline_path, encoding="utf-8") as f:
            baseline = set(json.load(f))
    signatures = []
    missing = 0
    for rt, field, klass, detail, enc, covered in findings:
        if covered == "info":
            continue
        sig = "%s|%s|%s|%s" % (rt, field, klass, enc)
        signatures.append(sig)
        known = " baseline" if sig in baseline else ""
        line = "MINE %s: %s: %s %s -> %s [%s%s]" % (
            rt, field, klass, detail, enc, covered, known)
        out.write(line + "\n")
        if covered == "MISSING" and sig not in baseline:
            missing += 1
    if update_baseline:
        with open(baseline_path, "w", encoding="utf-8") as f:
            json.dump(sorted(set(signatures)), f, indent=1)
            f.write("\n")
        out.write("baseline updated: %s (%d signatures)\n"
                  % (baseline_path, len(set(signatures))))
    for rt in fetch_failures:
        out.write("MINE %s: source file not found under expected names — "
                  "check the provider repo layout manually\n" % rt)
    out.write("\n%d finding(s), %d NEW missing coverage (vs baseline), "
              "%d resource(s) unfetchable\n"
              % (len(findings), missing, len(fetch_failures)))
    if missing:
        out.write("For each MISSING line: verify against the provider source "
                  "(tools/MINING.md has the per-class procedure), then add the "
                  "named override entry.\n")
        return 4
    return 0


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    pins = _load_provider_pins()
    shared_cache = {}
    findings = []
    fetch_failures = []
    for rt in generated_types():
        product = rt.split("_", 1)[0]
        version = pins.get(product)
        if not version:
            continue
        if product not in shared_cache:
            blob = ""
            for fn in LAYOUTS.get(product, LAYOUTS["zia"])["shared"]:
                try:
                    blob += _fetch(REPO % (product, version, fn))
                except Exception:
                    pass  # not every provider has every shared file
            shared_cache[product] = blob
        layout = LAYOUTS.get(product, LAYOUTS["zia"])
        short = rt.split("_", 1)[1]
        candidates = [FILE_ALIASES[rt]] if rt in FILE_ALIASES else [
            t.format(rt=rt, short=short) for t in layout["paths"]]
        source = None
        for candidate in candidates:
            try:
                source = _fetch(REPO % (product, version, candidate))
                break
            except Exception:
                continue
        if source is None:
            fetch_failures.append(rt)
            continue
        block_types = (load_resource(rt)["block"].get("block_types")) or {}
        scan_resource(rt, source, shared_cache[product], load_override(rt),
                      findings, block_types)

    return report(
        findings, fetch_failures,
        os.path.join("tools", "overrides", "mine-baseline.json"),
        bool(os.environ.get("UPDATE_BASELINE")), sys.stdout)


if __name__ == "__main__":
    sys.exit(main())
