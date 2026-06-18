"""Render a drift PR body from the worktree's config changes.

After `make drift` exits 3, the worktree holds the regenerated config;
this renders a reviewer-facing markdown summary: per resource, which
items were added (with import note), removed, or changed - and for
changed items, WHICH fields. Deterministic; the PR-creation pipeline
pipes stdout into the PR description.

Usage: python -m tools.drift_summary <tenant>

Stdlib-only, Python 3.6-floor - see AGENTS.md rule 5.
"""
import json
import os
import subprocess
import sys

from tools.registry import generated_types

CONFIG_SUFFIX = ".auto.tfvars.json"


def diff_items(old_items, new_items):
    """(old, new) -> (added_keys, removed_keys, {changed_key: [fields]})."""
    added = sorted(set(new_items) - set(old_items))
    removed = sorted(set(old_items) - set(new_items))
    changed = {}
    for key in sorted(set(old_items) & set(new_items)):
        fields = []
        o, n = old_items[key], new_items[key]
        for f in sorted(set(o) | set(n)):
            if o.get(f) != n.get(f):
                fields.append(f)
        if fields:
            changed[key] = fields
    return added, removed, changed


def committed_items(path):
    """The committed (HEAD) version of a config file's items, {} if new."""
    try:
        out = subprocess.check_output(
            ["git", "show", "HEAD:%s" % path], stderr=subprocess.DEVNULL
        )
    except subprocess.CalledProcessError:
        return {}
    return json.loads(out.decode("utf-8")).get("items") or {}


def worktree_items(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("items") or {}


MASS_CHANGE_THRESHOLD = 10
SAME_FIELD_THRESHOLD = 5


def _mass_change_banners(per_resource):
    """Banners for change shapes that smell like cascades or incidents,
    not routine click-ops: many items at once, or one FIELD changing
    across many items (e.g. ZIA's insert-shift order semantics cascade a
    single rule insert/delete into order changes on every other rule)."""
    banners = []
    for rt in sorted(per_resource):
        added, removed, changed = per_resource[rt]
        moved = len(added) + len(removed) + len(changed)
        if moved >= MASS_CHANGE_THRESHOLD:
            banners.append(
                "> :warning: **MASS CHANGE -- %s: %d item(s) affected.** "
                "This shape suggests a cascade (rule insert/delete "
                "shifting neighbors) or a bulk/scripted change, not "
                "routine click-ops. Review the audit table before "
                "merging." % (rt, moved)
            )
        field_counts = {}
        for fields in changed.values():
            for f in fields:
                field_counts[f] = field_counts.get(f, 0) + 1
        for f in sorted(field_counts):
            if field_counts[f] >= SAME_FIELD_THRESHOLD:
                banners.append(
                    "> :warning: **%s: `%s` changed on %d item(s).** One "
                    "field moving across many items usually means a "
                    "cascade (e.g. ZIA re-sequenced rule order after an "
                    "insert/delete) -- merging adopts the NEW arrangement "
                    "as truth; applying old config would re-shuffle the "
                    "tenant." % (rt, f, field_counts[f])
                )
    return banners


def render_summary(tenant, per_resource):
    """per_resource: {rt: (added, removed, changed)} -> markdown body."""
    lines = ["## Drift report: tenant `%s`" % tenant, ""]
    for banner in _mass_change_banners(per_resource):
        lines.append(banner)
        lines.append("")
    total = 0
    for rt in sorted(per_resource):
        added, removed, changed = per_resource[rt]
        if not (added or removed or changed):
            continue
        total += len(added) + len(removed) + len(changed)
        lines.append("### %s" % rt)
        for key in added:
            lines.append(
                "- **+ %s** -- new in tenant; import block staged in "
                "`imports/%s/`" % (key, tenant)
            )
        for key in removed:
            lines.append(
                "- **- %s** -- gone from tenant; merging removes it from "
                "config (state cleanup on next apply)" % key
            )
        for key, fields in sorted(changed.items()):
            lines.append("- **~ %s** -- changed: %s" % (key, ", ".join(fields)))
        lines.append("")
    if total == 0:
        return "No config-level drift.\n"
    lines.append(
        "Backfill PR: config now matches the tenant, so the plan on this "
        "PR should show **0 to add/change/destroy** (imports allowed). "
        "A non-zero plan means the tenant moved again -- re-run drift."
    )
    lines.append("")
    lines.append(
        "> If this PR appears to UNDO a recently merged change, and the "
        "audit table shows no matching console change, the likely cause "
        "is that the change was merged but never APPLIED -- check the "
        "delivery pipeline's apply stage before merging this."
    )
    return "\n".join(lines) + "\n"


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        sys.stderr.write("usage: python -m tools.drift_summary <tenant>\n")
        return 2
    tenant = argv[0]
    per_resource = {}
    for rt in generated_types():
        path = os.path.join("config", tenant, rt + CONFIG_SUFFIX)
        old = committed_items(path)
        new = worktree_items(path)
        if old or new:
            per_resource[rt] = diff_items(old, new)
    sys.stdout.write(render_summary(tenant, per_resource))
    return 0


if __name__ == "__main__":
    sys.exit(main())
