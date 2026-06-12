"""Tenant-aware credential resolution: prefixed env vars -> standard names.

Pipelines hold secrets once, under tenant-scoped names in either spelling:
tenant-first (ZS2_ZIA_USERNAME, ZS2_ZSCALER_CLOUD, ...) or product-first
(ZIA_ZS2_USERNAME, ZSCALER_ZS2_CLOUD, ...) — tenant-first wins when both
are set. This prints `export` lines remapping them to the standard names
the providers and fetcher read, so the per-tenant case statement lives in
testable code instead of YAML — and NO tenant-specific value (cloud names,
vanity domains) is ever encoded in this repo; the mapping is a mechanical
transform over an allowlist.

Usage:  eval "$(python -m tools.cred_env <tenant>)"
(Do not run under `set -x` — the export lines carry secret values.)

Stdlib-only, Python 3.6-floor — see AGENTS.md rule 5.
"""
import os
import re
import sys

# Every credential/targeting var the providers and fetcher read. Anything
# else under the prefix is ignored — the allowlist IS the contract.
STANDARD_VARS = (
    "ZSCALER_CLIENT_ID",
    "ZSCALER_CLIENT_SECRET",
    "ZSCALER_VANITY_DOMAIN",
    "ZSCALER_CLOUD",
    "ZSCALER_USE_LEGACY_CLIENT",
    "ZPA_CUSTOMER_ID",
    "ZIA_USERNAME",
    "ZIA_PASSWORD",
    "ZIA_API_KEY",
    "ZIA_CLOUD",
    "ZPA_CLIENT_ID",
    "ZPA_CLIENT_SECRET",
    "ZPA_CLOUD",
    "ZCC_CLIENT_ID",
    "ZCC_CLIENT_SECRET",
    "ZCC_CLOUD",
)


def tenant_prefix(tenant):
    """Opaque label -> env prefix: uppercase, non-alphanumerics to _."""
    return re.sub(r"[^A-Z0-9]", "_", tenant.upper()) + "_"


def candidate_names(name, tenant):
    """The two accepted spellings for one standard var, precedence order.

    Tenant-first is primary: ZS2_ZIA_USERNAME. Product-first is the
    fallback, with the tenant inserted after the product token:
    ZIA_ZS2_USERNAME (and ZSCALER_ZS2_CLIENT_ID). Both are mechanical
    transforms of the allowlist — no per-tenant facts live here.
    """
    prefix = tenant_prefix(tenant)
    product, _, rest = name.partition("_")
    return [prefix + name, product + "_" + prefix + rest]


def resolve(environ, tenant):
    """(prefixed env, tenant) -> sorted list of (standard_name, value).

    Returns (pairs, conflicts): conflicts lists standard names where the
    tenant-first and product-first spellings BOTH exist with different
    values — tenant-first wins, but silence would hide a likely
    misconfiguration, so callers print them to stderr.
    """
    out = []
    conflicts = []
    for name in STANDARD_VARS:
        names = candidate_names(name, tenant)
        values = [environ.get(n) for n in names]
        # set-but-EMPTY is missing: resolving '' would satisfy this layer
        # and push the failure to the fetch layer's "missing required env
        # var" — misleading when the var IS set, just blank (a common
        # secret-injection slip in pipelines)
        present = [v for v in values if v]
        if not present:
            continue
        out.append((name, present[0]))
        if len(present) == 2 and present[0] != present[1]:
            conflicts.append(name)
    return out, conflicts


def shell_quote(value):
    """Single-quote for POSIX shells ('' -> '\\'')."""
    return "'" + value.replace("'", "'\"'\"'") + "'"


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        sys.stderr.write("usage: eval \"$(python -m tools.cred_env <tenant>)\"\n")
        return 2
    tenant = argv[0]
    pairs, conflicts = resolve(os.environ, tenant)
    if not pairs:
        sys.stderr.write(
            "error: no credentials in the environment for tenant %r — "
            "set tenant-first vars (e.g. %sZSCALER_CLIENT_ID) or "
            "product-first vars (e.g. ZSCALER_%sCLIENT_ID)\n"
            % (tenant, tenant_prefix(tenant), tenant_prefix(tenant))
        )
        return 1
    for name in conflicts:
        sys.stderr.write(
            "warning: %s is set in BOTH spellings with different values; "
            "using the tenant-first one (%s)\n"
            % (name, candidate_names(name, tenant)[0])
        )
    for name, value in pairs:
        sys.stdout.write("export %s=%s\n" % (name, shell_quote(value)))
    sys.stderr.write(
        "resolved %d credential var(s) for tenant %r\n" % (len(pairs), tenant)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
