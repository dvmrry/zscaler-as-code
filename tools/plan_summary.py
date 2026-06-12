"""One markdown summary row per saved plan — the reviewer's first read.

stdin: `terraform show -json tfplan` output. argv[1]: the row label
(tenant/resource). Prints two lines: the markdown table row
(| label | imports | add | change | destroy |) and the destroy count
(consumed by plan-report to total a loud warning banner). A replace
counts as BOTH add and destroy — that is what it does.

Stdlib-only, Python 3.6-floor — see AGENTS.md rule 5.
"""
import json
import sys


def summarize(plan, label):
    """(plan JSON dict, label) -> (markdown_row, destroy_count).

    Raises ValueError when the document is not plan JSON — a version-
    skewed `terraform show` emits a different shape, and without this
    guard the reviewer's approval table silently shows all zeros. The
    apply/assert-clean recipes carry the same guard; the summary the
    human approves on must not be the one layer that can lie.
    """
    if not isinstance(plan, dict) or "format_version" not in plan:
        raise ValueError(
            "stdin is not plan JSON (no format_version — terraform "
            "version skew between agents?); re-run the plan stage")
    imports = adds = changes = destroys = 0
    for rc in plan.get("resource_changes") or []:
        change = rc.get("change") or {}
        actions = set(change.get("actions") or [])
        if change.get("importing") or rc.get("importing"):
            imports += 1
        if "create" in actions:
            adds += 1
        if "update" in actions:
            changes += 1
        if "delete" in actions:
            destroys += 1
    row = "| %s | %d | %d | %d | %d |" % (label, imports, adds, changes, destroys)
    return row, destroys


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        sys.stderr.write("usage: terraform show -json tfplan | "
                         "python -m tools.plan_summary <label>\n")
        return 2
    try:
        plan = json.load(sys.stdin)
        row, destroys = summarize(plan, argv[0])
    except ValueError as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 1
    sys.stdout.write(row + "\n")
    sys.stdout.write("%d\n" % destroys)
    return 0


if __name__ == "__main__":
    sys.exit(main())
