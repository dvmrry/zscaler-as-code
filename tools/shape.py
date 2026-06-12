"""Shape report: a sanitized, paste-able structural digest of plans,
config, and pulls — for relaying diagnostics OUT of restricted
environments where files can't leave but small sanitized text can.

Every value is replaced by a deterministic per-run token (n1/id1/s1...;
booleans and null stay literal); equal values get EQUAL tokens, so the
report still discriminates "list reordered" from "values rewritten"
without carrying a single real value. Item keys (derived from names)
are tokenized too. Field names come from the provider schema, not
tenant data, and stay readable.

Input auto-detected: terraform plan JSON (`terraform show -json
tfplan`) -> changed-paths-only diff per resource; transform tfvars
(`{"items": ...}`) or raw API pull -> full tokenized structure.

A self-check refuses to emit if any input string survived into the
output (defense in depth — the tokenizer already replaces every leaf
by construction).

Stdlib-only, Python 3.6-floor — see AGENTS.md rule 5.
"""
import json
import re
import sys

_ADDRESS_KEY = re.compile(r'\["([^"]*)"\]')

# Terraform's complete plan-action vocabulary; anything else in an
# actions list is not structural and must not be echoed.
_TF_ACTIONS = ("no-op", "create", "read", "update", "delete", "forget")

# Strings that legitimately appear in report structure (JSON literals,
# action names). A VALUE equal to one of these is still tokenized; the
# self-check just can't use substring presence as leak evidence for them.
_STRUCTURAL_WORDS = frozenset(
    ("true", "false", "null", "other-action") + _TF_ACTIONS)


def known_field_names():
    """Every dict key that is provably schema/metadata vocabulary, NOT
    tenant data: attribute + block names from ALL committed provider
    schemas (recursively), plus acknowledged_drops path segments (known
    API-only metadata like creation_time). Any key outside this set is
    tokenized — tenant data can hide in map KEYS (scim_attribute_header,
    custom headers), not just values."""
    from tools.registry import generated_types
    from tools.tfschema import load_resource
    from tools.transform import load_override

    names = {"items"}

    def walk(blk):
        for a in blk.get("attributes") or {}:
            names.add(a)
        for b, bt in (blk.get("block_types") or {}).items():
            names.add(b)
            walk(bt.get("block") or {})

    for rt in generated_types():
        walk(load_resource(rt)["block"])
        for dropped in load_override(rt).get("acknowledged_drops") or []:
            for seg in dropped.replace("[]", "").split("."):
                if seg:
                    names.add(seg)
    return frozenset(names)


class Tokenizer(object):
    """Deterministic value -> token map. Same value, same token."""

    def __init__(self):
        self.tokens = {}
        self.counts = {"s": 0, "id": 0, "n": 0, "k": 0}

    def _next(self, prefix):
        self.counts[prefix] += 1
        return "%s%d" % (prefix, self.counts[prefix])

    def token(self, value, prefix=None):
        if isinstance(value, bool) or value is None:
            return json.dumps(value)
        if isinstance(value, (int, float)):
            # 1 and 1.0 are the same JSON number — same token, or a
            # provider int/float round-trip reads as a phantom change.
            if isinstance(value, float) and value.is_integer():
                value = int(value)
            key = ("num", repr(value))
            if key not in self.tokens:
                self.tokens[key] = self._next("n")
            return self.tokens[key]
        text = "%s" % (value,)
        key = ("str", text)
        if key not in self.tokens:
            if prefix is None:
                prefix = "id" if text.isdigit() else "s"
            self.tokens[key] = self._next(prefix)
        return self.tokens[key]


def _key_is_safe(key, allow):
    from tools.transform import snake

    return key in allow or snake(key) in allow


def sanitize(value, tok, allow, key_depth=-1):
    """Tokenized copy of a JSON structure. A dict KEY survives only if
    it is known schema/metadata vocabulary (`allow`, see
    known_field_names) — and never at key_depth 0, where keys ARE data
    (the items map keys derive from tenant object names)."""
    if isinstance(value, dict):
        out = {}
        for k in sorted(value):
            if key_depth == 0 or not _key_is_safe(k, allow):
                new_k = tok.token(k, prefix="k")
            else:
                new_k = k
            out[new_k] = sanitize(value[k], tok, allow, key_depth - 1)
        return out
    if isinstance(value, list):
        return [sanitize(v, tok, allow, -1) for v in value]
    if isinstance(value, str):
        return tok.token(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return tok.token(value)
    return value


def _canon(value, tok, allow):
    return json.dumps(sanitize(value, tok, allow), sort_keys=True)


def diff_lines(before, after, tok, allow, path, lines):
    """Changed paths only. Lists: detect pure permutations and emit the
    index mapping instead of N spurious in-place edits. Dict keys in
    emitted paths obey the same allowlist as sanitize()."""
    if isinstance(before, dict) and isinstance(after, dict):
        for k in sorted(set(before) | set(after)):
            shown = k if _key_is_safe(k, allow) else tok.token(k, prefix="k")
            child = "%s.%s" % (path, shown) if path else shown
            if k not in before:
                lines.append("  + %s = %s" % (child, _canon(after[k], tok, allow)))
            elif k not in after:
                lines.append("  - %s (was %s)"
                             % (child, _canon(before[k], tok, allow)))
            else:
                diff_lines(before[k], after[k], tok, allow, child, lines)
        return
    if isinstance(before, list) and isinstance(after, list):
        b = [_canon(v, tok, allow) for v in before]
        a = [_canon(v, tok, allow) for v in after]
        if b == a:
            return
        if sorted(b) == sorted(a):
            pool = list(b)
            order = []
            for elem in a:
                i = pool.index(elem)
                pool[i] = None
                order.append(i)
            lines.append("  ~ %s: REORDER only — new order takes old "
                         "indexes %s" % (path, order))
            return
        for i in range(max(len(before), len(after))):
            child = "%s[%d]" % (path, i)
            if i >= len(before):
                lines.append("  + %s = %s" % (child, _canon(after[i], tok, allow)))
            elif i >= len(after):
                lines.append("  - %s (was %s)"
                             % (child, _canon(before[i], tok, allow)))
            else:
                diff_lines(before[i], after[i], tok, allow, child, lines)
        return
    b, a = _canon(before, tok, allow), _canon(after, tok, allow)
    if b != a:
        lines.append("  ~ %s: %s -> %s" % (path, b, a))


def _sanitize_address(address, tok):
    return _ADDRESS_KEY.sub(
        lambda m: '["%s"]' % tok.token(m.group(1), prefix="k"), address)


def shape_plan(doc, tok, allow, only=None):
    lines = []
    counts = {}
    for rc in doc.get("resource_changes") or []:
        raw_actions = (rc.get("change") or {}).get("actions") or []
        # terraform's action vocabulary is closed; anything else is not
        # structural and must not be echoed
        actions = [a if a in _TF_ACTIONS else "other-action"
                   for a in raw_actions]
        for act in actions:
            counts[act] = counts.get(act, 0) + 1
        if actions in ([], ["no-op"], ["read"]):
            continue
        if only and only not in (rc.get("type") or rc.get("address") or ""):
            continue
        change = rc.get("change") or {}
        lines.append("%s %s (%s)" % (
            "~" if "update" in actions else "*",
            _sanitize_address(rc.get("address") or "?", tok),
            "+".join(actions)))
        importing = change.get("importing") or {}
        if importing.get("id"):
            lines.append("  importing id %s" % tok.token(importing["id"]))
        diff_lines(change.get("before") or {}, change.get("after") or {},
                   tok, allow, "", lines)
    header = "plan: " + ", ".join(
        "%d %s" % (counts[k], k) for k in sorted(counts)) if counts else \
        "plan: no resource changes"
    return [header] + lines


def collect_strings(value, out, allow, key_depth=-1):
    """Every string that must NOT survive into the report: all string
    leaves plus every dict key that is data (item keys, anything not in
    the schema-vocabulary allowlist)."""
    if isinstance(value, dict):
        for k, v in value.items():
            if key_depth == 0 or not _key_is_safe(k, allow):
                out.add(k)
            collect_strings(v, out, allow, key_depth - 1)
    elif isinstance(value, list):
        for v in value:
            collect_strings(v, out, allow, -1)
    elif isinstance(value, str):
        out.add(value)


def _kept_keys(value, out, allow, key_depth=-1):
    if isinstance(value, dict):
        for k, v in value.items():
            if key_depth != 0 and _key_is_safe(k, allow):
                out.add(k)
            _kept_keys(v, out, allow, key_depth - 1)
    elif isinstance(value, list):
        for v in value:
            _kept_keys(v, out, allow, -1)


def self_check(report_text, secrets, kept):
    """Refuse to emit a report any input data string survived into.

    `secrets` is every string from the input's DATA regions (values,
    item keys, address-embedded keys) — structural strings like action
    names or resource types are not data and are not collected. Strings
    that are ALSO schema field names in the input are exempt (a value
    happening to equal a key like "name" is replaced as a value; the
    key legitimately appears), as are JSON/action literals ("true",
    "create": such a value IS tokenized — the word in the report is
    structure, so its presence proves nothing). Short strings (<4
    chars) collide with structural text too easily to test."""
    leaked = sorted(
        s for s in secrets
        if len(s) >= 4 and s not in kept
        and s not in _STRUCTURAL_WORDS and s in report_text)
    if leaked:
        sys.stderr.write(
            "shape: SELF-CHECK FAILED — %d input value(s) would leak into "
            "the report; refusing to emit. This is a bug in tools/shape.py "
            "— do not paste any partial output.\n" % len(leaked))
        return False
    return True


def _plan_data_regions(doc, allow):
    """The data-bearing regions of a plan JSON: change before/after/
    importing per resource, output values, and the tenant-derived keys
    embedded in addresses and for_each indexes."""
    secrets, kept = set(), set()
    for rc in doc.get("resource_changes") or []:
        change = rc.get("change") or {}
        for region in (change.get("before"), change.get("after"),
                       change.get("importing")):
            collect_strings(region, secrets, allow, -1)
            _kept_keys(region, kept, allow, -1)
        for act in change.get("actions") or []:
            if act not in _TF_ACTIONS:
                secrets.add(act)
        index = rc.get("index")
        if isinstance(index, str):
            secrets.add(index)
        for m in _ADDRESS_KEY.finditer(rc.get("address") or ""):
            secrets.add(m.group(1))
    for change in (doc.get("output_changes") or {}).values():
        collect_strings(change.get("before"), secrets, allow, -1)
        collect_strings(change.get("after"), secrets, allow, -1)
    return secrets, kept


def build_report(doc, only=None):
    """Returns (report_text, secrets, kept_keys) — the caller MUST run
    self_check before letting the text out."""
    tok = Tokenizer()
    allow = known_field_names()
    if isinstance(doc, dict) and "format_version" in doc and (
            "resource_changes" in doc or "planned_values" in doc):
        kind = "plan"
        lines = shape_plan(doc, tok, allow, only=only)
        secrets, kept = _plan_data_regions(doc, allow)
    elif isinstance(doc, dict) and isinstance(doc.get("items"), dict):
        kind = "tfvars"
        lines = json.dumps(sanitize(doc, tok, allow, key_depth=1),
                           indent=1, sort_keys=True).splitlines()
        secrets, kept = set(), set()
        collect_strings(doc, secrets, allow, 1)
        _kept_keys(doc, kept, allow, 1)
    else:
        kind = "raw"
        lines = json.dumps(sanitize(doc, tok, allow), indent=1,
                           sort_keys=True).splitlines()
        secrets, kept = set(), set()
        collect_strings(doc, secrets, allow, -1)
        _kept_keys(doc, kept, allow, -1)
    body = "\n".join(lines)
    text = ("shape report (%s): values are per-run tokens — equal token = "
            "equal value; no tenant data.\n%s\n" % (kind, body))
    return text, secrets, kept


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or len(argv) > 2:
        sys.stderr.write(
            "usage: python -m tools.shape <file.json> [resource-type-filter]\n"
            "  file: terraform plan JSON (terraform show -json tfplan), a\n"
            "  config *.auto.tfvars.json, or a raw pull. Output is fully\n"
            "  sanitized and safe to relay.\n")
        return 2
    try:
        with open(argv[0], encoding="utf-8") as f:
            doc = json.load(f)
    except ValueError:
        sys.stderr.write("error: %s is not valid JSON (content withheld — "
                         "check the file by hand)\n" % argv[0])
        return 1
    except OSError as exc:
        sys.stderr.write("error: cannot read %s: %s\n" % (argv[0], exc))
        return 1
    text, secrets, kept = build_report(
        doc, only=argv[1] if len(argv) > 1 else None)
    if not self_check(text, secrets, kept):
        return 1
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
