"""Classify unacknowledged drop-report fields — the triage, mechanized.

The estate-wide drop triage decomposed almost entirely into mechanical
rules; this tool encodes them so a red DROPS_CHECK run is followed by
ONE command, not a manual review. Per dropped path, in order:

  SYNONYM    leaf shares a non-generic token with a schema field at the
             SAME level — the signingCertId class (API name != schema
             name, possibly write-required). NEVER auto-acknowledged;
             goes on the worklist with the rename recipe.
  DECORATION nested under a block whose schema inputs are id-only: no
             field but id can EXIST in config, so acknowledging is the
             only possible encode. Safe by construction.
  METADATA   audit/identity vocabulary (timestamps, modified_by, ...)
             or a computed-only schema attr (provider knows it,
             nobody can set it). Safe.
  SDK        the vendor Go SDK models the field (json tag present) but
             the provider never wired it — console-managed feature,
             upstream-support candidate. Safe to acknowledge; noted.
  UNKNOWN    none of the above — worklist, MINING.md procedure.

APPLY=1 writes the safe classes into each override's
acknowledged_drops. Exit: 0 = nothing left for eyes; 4 = worklist
items remain (SYNONYM/UNKNOWN — make flattens to 2; red run is the
signal); 1 = failure. SDK lane needs network (degrades to UNKNOWN
with a note when unreachable).

Stdlib-only, Python 3.6-floor — see AGENTS.md rule 5.
"""
import json
import os
import ssl
import sys

from tools.registry import generated_types
from tools.tfschema import classify_attributes, load_resource
from tools.transform import OVERRIDES_DIR, load_override, transform_items

SDK_TAG = "v3.8.37"
SDK_PATHS = {
    "zcc_failopen_policy": "zscaler/zcc/services/failopen_policy/failopen_policy.go",
    "zcc_forwarding_profile": "zscaler/zcc/services/forwarding_profile/forwarding_profile.go",
    "zcc_trusted_network": "zscaler/zcc/services/trusted_network_v2/trusted_network_v2.go",
    "zcc_web_privacy": "zscaler/zcc/services/web_privacy/web_privacy.go",
    "zia_cloud_app_control_rule": "zscaler/zia/services/cloudappcontrol/cloudappcontrol.go",
    "zia_location_management": "zscaler/zia/services/location/locationmanagement/locationmanagement.go",
    "zia_rule_labels": "zscaler/zia/services/rule_labels/rule_labels.go",
    "zia_ssl_inspection_rules": "zscaler/zia/services/sslinspection/sslinspection.go",
    "zia_url_categories": "zscaler/zia/services/urlcategories/urlcategories.go",
    "zia_url_filtering_rules": "zscaler/zia/services/urlfilteringpolicies/urlfilteringpolicies.go",
    "zpa_app_connector_group": "zscaler/zpa/services/appconnectorgroup/zpa_app_connector_group.go",
    "zpa_application_segment": "zscaler/zpa/services/applicationsegment/zpa_application_segment.go",
    "zpa_application_server": "zscaler/zpa/services/appservercontroller/zpa_app_server_controller.go",
    "zpa_policy_access_rule": "zscaler/zpa/services/policysetcontroller/policysetcontroller.go",
    "zpa_segment_group": "zscaler/zpa/services/segmentgroup/zpa_segment_group.go",
    "zpa_server_group": "zscaler/zpa/services/servergroup/zpa_server_group.go",
}

# Audit/identity vocabulary: read-only by nature, never configuration.
METADATA_VOCAB = frozenset((
    "id", "guid", "creation_time", "created_by", "modified_by",
    "modified_time", "last_modified_by", "last_modified_time",
    "read_only", "zscaler_managed", "microtenant_name", "child_count",
    "non_editable", "referenced_rule_count",
))

# Tokens too generic to indicate a synonym on their own.
GENERIC_TOKENS = frozenset((
    "id", "ids", "name", "type", "state", "enabled", "time", "by",
    "count", "is", "in", "to", "of",
))


def _camel(snake):
    parts = snake.split("_")
    return parts[0] + "".join(p.title() for p in parts[1:])


def _tokens(field):
    return set(field.split("_")) - GENERIC_TOKENS


def _resolve_parent_block(block, segs):
    """Walk dotted path segments (sans the leaf) through nested blocks.
    Returns the parent block dict, or None when the path doesn't follow
    schema blocks (e.g. the parent itself is unknown to the schema)."""
    cur = block
    for seg in segs:
        bt = (cur.get("block_types") or {}).get(seg)
        if bt is None:
            return None
        cur = bt["block"]
    return cur


def fetch_sdk_text(rt):
    import urllib.request

    path = SDK_PATHS.get(rt)
    if not path:
        return None
    bundle = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    ctx = ssl.create_default_context(cafile=bundle) if bundle else None
    url = ("https://raw.githubusercontent.com/zscaler/zscaler-sdk-go/%s/%s"
           % (SDK_TAG, path))
    with urllib.request.urlopen(url, context=ctx, timeout=30) as resp:
        return resp.read().decode("utf-8", "replace")


def classify(rt, path, sdk_text=None):
    """One dropped path -> (CLASS, explanation). sdk_text=None means the
    SDK lane is unavailable (offline / unmapped resource)."""
    block = load_resource(rt)["block"]
    segs = path.replace("[]", "").split(".")
    leaf = segs[-1]
    parent = _resolve_parent_block(block, segs[:-1])

    # 1. SYNONYM — the dangerous class, checked FIRST and never auto-
    #    acknowledged. Compare at the SAME nesting level only (a nested
    #    'description' must not match the top-level attr).
    if parent is not None:
        level_names = set(parent.get("attributes") or {}) | set(
            parent.get("block_types") or {})
        leaf_tokens = _tokens(leaf)
        for cand in sorted(level_names - {leaf}):
            if leaf_tokens & _tokens(cand):
                return ("SYNONYM", "shares tokens with schema field %r — "
                        "the signingCertId class; verify the provider read/"
                        "expand (tools/MINING.md), then encode renames "
                        "{\"%s\": \"%s\"} or acknowledge" % (cand, leaf, cand))

    # 2. DECORATION — inner field of an id-only reference block: config
    #    can never carry it, so acknowledging is the only possible encode.
    if parent is not None and parent is not block:
        cls = classify_attributes(parent)
        inputs = set(cls["required"] + cls["optional"])
        if inputs <= {"id"} and leaf != "id":
            return ("DECORATION", "inner field of an id-only reference "
                    "block — cannot exist in config")
        if leaf == "id" or inputs <= {"id"}:
            return ("DECORATION", "reference-block metadata")

    # 3. METADATA — audit vocabulary, or computed-only in the schema.
    if leaf in METADATA_VOCAB:
        return ("METADATA", "audit/identity field — read-only by nature")
    if parent is not None:
        cls = classify_attributes(parent)
        if leaf in cls.get("computed_only", []):
            return ("METADATA", "computed-only in the provider schema")

    # 4. SDK — vendor models it, provider ignores it.
    if sdk_text is None:
        return ("UNKNOWN", "no SDK text (offline or unmapped resource) — "
                "grep the vendor SDK struct for json:\"%s\", then follow "
                "tools/MINING.md" % _camel(leaf))
    if 'json:"%s' % _camel(leaf) in sdk_text:
        return ("SDK", "SDK-modeled but provider-ignored — console-managed "
                "feature; upstream-support candidate")
    return ("UNKNOWN", "not in schema, SDK, or any safe class — follow "
            "tools/MINING.md (issue-watch may already explain it)")


SAFE_CLASSES = ("DECORATION", "METADATA", "SDK")


def acknowledge(rt, paths):
    p = os.path.join(OVERRIDES_DIR, rt + ".json")
    data = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
    before = set(data.get("acknowledged_drops") or [])
    data["acknowledged_drops"] = sorted(before | set(paths))
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        sys.stderr.write(
            "usage: python -m tools.triage <pulls_dir>   "
            "(make triage IN=pulls/<tenant> [APPLY=1])\n")
        return 2
    pulls = argv[0]
    apply_safe = bool(os.environ.get("APPLY"))
    worklist = 0
    acknowledged = 0
    for rt in generated_types():
        src = os.path.join(pulls, rt + ".json")
        if not os.path.exists(src):
            continue
        with open(src, encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, list):
            sys.stderr.write("error: %s is not a JSON list — re-run make "
                             "fetch\n" % src)
            return 1
        _, _, reported = transform_items(raw, rt, load_override(rt))
        if not reported:
            continue
        try:
            sdk_text = fetch_sdk_text(rt)
        except Exception:
            sdk_text = None
        safe = []
        for path in reported:
            klass, why = classify(rt, path, sdk_text)
            sys.stdout.write("TRIAGE %s: %s: %s — %s\n"
                             % (rt, path, klass, why))
            if klass in SAFE_CLASSES:
                safe.append(path)
            else:
                worklist += 1
        if safe and apply_safe:
            acknowledge(rt, safe)
            acknowledged += len(safe)
    if acknowledged:
        sys.stdout.write("\n%d safe path(s) written to acknowledged_drops "
                         "— commit the override changes\n" % acknowledged)
    elif not apply_safe:
        sys.stdout.write("\n(dry run — APPLY=1 writes the safe classes to "
                         "acknowledged_drops)\n")
    sys.stdout.write("%d path(s) need eyes (SYNONYM/UNKNOWN)\n" % worklist)
    if worklist:
        sys.stdout.write("For each: tools/MINING.md has the verification "
                         "procedure; never acknowledge a SYNONYM without "
                         "checking the provider read/expand first.\n")
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
