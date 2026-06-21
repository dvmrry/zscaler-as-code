"""Derive (tenant, resource_type) plan targets from a git diff.

The repo layout encodes the mapping: a change to
config/<tenant>/<type>.auto.tfvars.json, its generated
lookups/<tenant>/<type>.lookup.json sidecar (or the matching imports/ or envs/
paths) affects exactly one (tenant, type) pair. A change to
modules/<type>/ affects that type on every tenant that has config for it. A
change to the shared machinery (tools/, schemas/, Makefile) affects everything,
so every configured pair is emitted — plans are read-only, so over-planning is
safe; under-planning is not.

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

from tools import deployment
from tools.registry import derive_entry, derived_types, generated_types

CONFIG_SUFFIX = ".auto.tfvars.json"
LOOKUP_SUFFIX = ".lookup.json"
IMPORTS_SUFFIX = "_imports.tf"
MOVES_SUFFIX = "_moves.tf"
# Shared machinery whose change invalidates every plannable pair. deployment.json
# repoints the overlay root, so a change to it (like a Makefile/tools change) must
# fan out to everything — under-planning an overlay repoint is the unsafe direction.
# A `local.mk` change is handled by basename match in pairs_from_paths (it can live
# at root OR under the overlay, which a startswith-prefix can't express).
GLOBAL_PREFIXES = ("tools/", "schemas/", "Makefile", "deployment.json")


def _overlay_roots(kind):
    """Roots to scan for `kind` (config|envs): the template root always, plus
    the overlay's `<overlay>/<kind>` when an overlay is set. At overlay="."
    this is just the root (no double-scan). Dedup is implicit — at default the
    two collapse to one path."""
    roots = [kind]
    ov = deployment.overlay()
    if ov != ".":
        roots.append(os.path.join(ov, kind))
    return roots


def _scan_config_root(config_root, types, pairs):
    if not os.path.isdir(config_root):
        return
    for tenant in sorted(os.listdir(config_root)):
        tdir = os.path.join(config_root, tenant)
        if not os.path.isdir(tdir):
            continue
        for fname in sorted(os.listdir(tdir)):
            if fname.endswith(CONFIG_SUFFIX):
                rt = fname[: -len(CONFIG_SUFFIX)]
                if rt in types:
                    pairs.add((tenant, rt))


def discover_config_pairs():
    """All (tenant, resource_type) pairs with a committed config file, unioned
    over the template root and the overlay root (deduped)."""
    pairs = set()
    types = set(generated_types())
    for root in _overlay_roots("config"):
        _scan_config_root(root, types, pairs)
    return pairs


def _scan_env_root(envs_root, types, pairs):
    if not os.path.isdir(envs_root):
        return
    for tenant in sorted(os.listdir(envs_root)):
        tdir = os.path.join(envs_root, tenant)
        if not os.path.isdir(tdir):
            continue
        for rt in sorted(os.listdir(tdir)):
            if rt in types and os.path.isdir(os.path.join(tdir, rt)):
                pairs.add((tenant, rt))


def discover_env_root_pairs():
    """All (tenant, resource_type) pairs that have a generated env ROOT —
    what `make plan` actually operates on — unioned over the template root and
    the overlay root (deduped). A pair can have a root but no config (its config
    was DELETED upstream to remove the resource): planning it shows the destroy,
    which is exactly what a deletion must surface. So this is unioned with the
    config pairs to bound plan-changed expansion — without it a delete-only commit
    plans nothing and the removal is lost."""
    pairs = set()
    types = set(generated_types())
    for root in _overlay_roots("envs"):
        _scan_env_root(root, types, pairs)
    return pairs


def _strip_overlay(path, overlay):
    """Drop a leading `<overlay>/` so an overlay-resident diff path (e.g.
    `_local/config/<t>/…`) splits on the same `config/|imports/|envs/` prefixes
    as a root path. No-op at overlay="." (root is the only root)."""
    if overlay and overlay != ".":
        prefix = overlay + "/"
        if path.startswith(prefix):
            return path[len(prefix):]
    return path


def pairs_from_paths(paths, plannable=None, overlay=None):
    """Map changed file paths to the (tenant, type) pairs they affect.

    `plannable` bounds every expansion: a pair is only ever emitted if it has
    a committed config OR an existing env root, so renames/typos can't fan out
    into nonexistent roots — while a DELETED config (root still present) and a
    `_moves.tf`-only rename still plan, instead of being silently dropped.
    `plannable=None` means unbounded (accept every syntactically valid pair) —
    callers that need the safety bound pass the discovery union explicitly
    (main() does); the global-machinery fan-out then uses that same union.

    `overlay` (default `deployment.overlay()`) lets an overlay-resident diff
    path strip its leading `<overlay>/` and match the same prefixes as a root
    path — a real tenant's config lives at `$(OVERLAY)/config/<t>/…`, which a
    bare `config/` startswith would otherwise miss.
    """
    if overlay is None:
        overlay = deployment.overlay()
    bound = plannable  # None => unbounded
    # The "everything" set for shared-machinery fan-out: the explicit bound when
    # given, else the live discovery union (never None — set(None) would raise).
    everything = bound if bound is not None else (
        discover_config_pairs() | discover_env_root_pairs())

    def _ok(pair):
        return bound is None or pair in bound

    pairs = set()
    for raw in paths:
        path = _strip_overlay(raw, overlay)
        parts = path.split("/")
        if path.startswith("config/") and len(parts) == 3 and parts[2].endswith(CONFIG_SUFFIX):
            pair = (parts[1], parts[2][: -len(CONFIG_SUFFIX)])
            if _ok(pair):
                pairs.add(pair)
        elif path.startswith("lookups/") and len(parts) == 3 and parts[2].endswith(LOOKUP_SUFFIX):
            pair = (parts[1], parts[2][: -len(LOOKUP_SUFFIX)])
            if _ok(pair):
                pairs.add(pair)
        elif path.startswith("config/") and len(parts) == 3 and parts[2].endswith(LOOKUP_SUFFIX):
            # Legacy pre-lookups lookup sidecars still show up as deletes during
            # migration and should plan the same resource.
            pair = (parts[1], parts[2][: -len(LOOKUP_SUFFIX)])
            if _ok(pair):
                pairs.add(pair)
        elif path.startswith("imports/") and len(parts) == 3 and (
                parts[2].endswith(IMPORTS_SUFFIX) or parts[2].endswith(MOVES_SUFFIX)):
            suffix = (IMPORTS_SUFFIX if parts[2].endswith(IMPORTS_SUFFIX)
                      else MOVES_SUFFIX)
            pair = (parts[1], parts[2][: -len(suffix)])
            if _ok(pair):
                pairs.add(pair)
        elif path.startswith("envs/") and len(parts) >= 3:
            pair = (parts[1], parts[2])
            if _ok(pair):
                pairs.add(pair)
        elif path.startswith("modules/") and len(parts) >= 2:
            rt = parts[1]
            for tenant, pair_rt in everything:
                if pair_rt == rt:
                    pairs.add((tenant, rt))
        elif path.startswith(GLOBAL_PREFIXES) or os.path.basename(path) == "local.mk":
            # Shared machinery (incl. deployment.json repointing the overlay, and
            # local.mk at root OR under the overlay): every plannable pair (derived
            # included) is in scope, so no separate derived expansion is needed.
            return set(everything)
    return expand_derived(pairs, everything)


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
