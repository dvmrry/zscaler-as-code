"""Derive (tenant, resource_type) plan targets from a git diff.

The repo layout encodes the mapping: a change to
config/<tenant>/<type>.auto.tfvars.json (or the matching imports/ or
envs/ paths) affects exactly one (tenant, type) pair. A change to
modules/<type>/ affects that type on every tenant that has config for
it. A change to the shared machinery (tools/, schemas/, Makefile)
affects everything, so every configured pair is emitted — plans are
read-only, so over-planning is safe; under-planning is not.

Output: one "tenant resource_type" line per pair, sorted. Empty output
with exit 0 means the diff touched nothing plannable (docs-only change)
— a PR pipeline must treat that as success, not failure.

Pass --tenant <label> to emit only that tenant's pairs: a per-tenant
delivery pipeline authenticates with one tenant's credentials (the auth
template's tenant is compile-time), so it must not try to plan a foreign
tenant a cross-tenant merge happened to touch.

A change to a SOURCE type also plans its DERIVED dependents (e.g. a change
to zpa_policy_access_rule also plans zpa_policy_access_rule_reorder). The
derived resource MUTATES on apply — the reorder re-sequences the rules — and
its config no longer travels with the source (rule order is deprecated off
the access rule), so a rule change can move ordering even when the reorder
config didn't change in the diff. Planning it whenever its source is in scope
keeps that mutation in a plan the reviewer actually sees; if it has nothing
to do the plan is a no-op they can confirm.

Stdlib-only, Python 3.6-floor — see AGENTS.md rule 5.
"""
import os
import subprocess
import sys

from tools.registry import derive_entry, derived_types, generated_types

CONFIG_SUFFIX = ".auto.tfvars.json"
IMPORTS_SUFFIX = "_imports.tf"
MOVES_SUFFIX = "_moves.tf"
GLOBAL_PREFIXES = ("tools/", "schemas/", "Makefile")


def discover_config_pairs(config_root="config"):
    """All (tenant, resource_type) pairs with a committed config file."""
    pairs = set()
    if not os.path.isdir(config_root):
        return pairs
    types = set(generated_types())
    for tenant in sorted(os.listdir(config_root)):
        tdir = os.path.join(config_root, tenant)
        if not os.path.isdir(tdir):
            continue
        for fname in sorted(os.listdir(tdir)):
            if fname.endswith(CONFIG_SUFFIX):
                rt = fname[: -len(CONFIG_SUFFIX)]
                if rt in types:
                    pairs.add((tenant, rt))
    return pairs


def discover_env_root_pairs(envs_root="envs"):
    """All (tenant, resource_type) pairs that have a generated env ROOT —
    what `make plan` actually operates on. A pair can have a root but no
    config (its config was DELETED upstream to remove the resource): planning
    it shows the destroy, which is exactly what a deletion must surface. So
    this is unioned with the config pairs to bound plan-changed expansion —
    without it a delete-only commit plans nothing and the removal is lost."""
    pairs = set()
    if not os.path.isdir(envs_root):
        return pairs
    types = set(generated_types())
    for tenant in sorted(os.listdir(envs_root)):
        tdir = os.path.join(envs_root, tenant)
        if not os.path.isdir(tdir):
            continue
        for rt in sorted(os.listdir(tdir)):
            if rt in types and os.path.isdir(os.path.join(tdir, rt)):
                pairs.add((tenant, rt))
    return pairs


def pairs_from_paths(paths, plannable):
    """Map changed file paths to the (tenant, type) pairs they affect.

    `plannable` bounds every expansion: a pair is only ever emitted if it has
    a committed config OR an existing env root, so renames/typos can't fan out
    into nonexistent roots — while a DELETED config (root still present) and a
    `_moves.tf`-only rename still plan, instead of being silently dropped.
    """
    pairs = set()
    for path in paths:
        parts = path.split("/")
        if path.startswith("config/") and len(parts) == 3 and parts[2].endswith(CONFIG_SUFFIX):
            pair = (parts[1], parts[2][: -len(CONFIG_SUFFIX)])
            if pair in plannable:
                pairs.add(pair)
        elif path.startswith("imports/") and len(parts) == 3 and (
                parts[2].endswith(IMPORTS_SUFFIX) or parts[2].endswith(MOVES_SUFFIX)):
            suffix = (IMPORTS_SUFFIX if parts[2].endswith(IMPORTS_SUFFIX)
                      else MOVES_SUFFIX)
            pair = (parts[1], parts[2][: -len(suffix)])
            if pair in plannable:
                pairs.add(pair)
        elif path.startswith("envs/") and len(parts) >= 3:
            pair = (parts[1], parts[2])
            if pair in plannable:
                pairs.add(pair)
        elif path.startswith("modules/") and len(parts) >= 2:
            rt = parts[1]
            for tenant, pair_rt in plannable:
                if pair_rt == rt:
                    pairs.add((tenant, rt))
        elif path.startswith(GLOBAL_PREFIXES):
            # Shared machinery: every plannable pair (derived included) is
            # already in scope, so no separate derived expansion is needed.
            return set(plannable)
    return expand_derived(pairs, plannable)


def _derived_by_source():
    """source resource_type -> set of derived types that track it
    (e.g. zpa_policy_access_rule -> {zpa_policy_access_rule_reorder})."""
    out = {}
    for dt in derived_types():
        src = (derive_entry(dt) or {}).get("from")
        if src:
            out.setdefault(src, set()).add(dt)
    return out


def expand_derived(pairs, plannable):
    """Add each selected pair's DERIVED dependents, bounded by `plannable`.

    A derived resource mutates on apply (zpa_policy_access_rule_reorder
    re-sequences the rules) and must be validated in the SAME plan as the
    source it tracks — never applied from a plan the reviewer never saw. Its
    config can stay byte-identical while the source moves ordering, so select
    it whenever its source is in scope; a tenant without the derived root/config
    (it isn't in `plannable`) is left alone. One level — the registry has no
    derived-of-derived chains.
    """
    by_source = _derived_by_source()
    if not by_source:
        return pairs
    extra = set()
    for tenant, rt in pairs:
        for dt in by_source.get(rt, ()):
            if (tenant, dt) in plannable:
                extra.add((tenant, dt))
    return pairs | extra


def changed_paths(base_ref):
    """Changed files vs the merge-base with base_ref (three-dot diff —
    exactly what a PR pipeline wants: the branch's own changes only)."""
    out = subprocess.check_output(
        ["git", "diff", "--name-only", base_ref + "...HEAD"]
    )
    return [line for line in out.decode().splitlines() if line.strip()]


_USAGE = "usage: python -m tools.changed <base-ref> [--tenant <label>]\n"


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    base_ref = None
    tenant = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--tenant" and i + 1 < len(argv):
            i += 1
            tenant = argv[i]
        elif a.startswith("-") or base_ref is not None:
            sys.stderr.write(_USAGE)
            return 2
        else:
            base_ref = a
        i += 1
    if base_ref is None:
        sys.stderr.write(_USAGE)
        return 2
    try:
        paths = changed_paths(base_ref)
    except subprocess.CalledProcessError:
        sys.stderr.write(
            "error: git diff against %r failed (unknown ref? shallow clone "
            "without the base? fetch it first)\n" % base_ref
        )
        return 2
    pairs = pairs_from_paths(
        paths, discover_config_pairs() | discover_env_root_pairs()
    )
    # A per-tenant delivery pipeline authenticates with ONE tenant's creds
    # (the auth template's tenant is compile-time), so it must plan only that
    # tenant's changed pairs — a cross-tenant merge otherwise tries to plan a
    # foreign tenant with the wrong credentials. Filter to the named tenant.
    if tenant is not None:
        pairs = {(t, rt) for (t, rt) in pairs if t == tenant}
    for t, rt in sorted(pairs):
        sys.stdout.write("%s %s\n" % (t, rt))
    if not pairs:
        sys.stderr.write(
            "no plannable changes vs %s%s\n"
            % (base_ref, " for tenant %s" % tenant if tenant else "")
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
