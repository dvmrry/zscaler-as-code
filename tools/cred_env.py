"""Tenant-aware credential resolution: prefixed env vars -> standard names.

Pipelines hold secrets once, under tenant-prefixed names (ZS2_ZIA_USERNAME,
ZS2_ZSCALER_CLOUD, ...). This prints `export` lines remapping them to the
standard names the providers and fetcher read, so the per-tenant case
statement lives in testable code instead of YAML — and NO tenant-specific
value (cloud names, vanity domains) is ever encoded in this repo; the
mapping is a mechanical prefix strip over an allowlist.

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


def resolve(environ, tenant):
    """(prefixed env, tenant) -> sorted list of (standard_name, value)."""
    prefix = tenant_prefix(tenant)
    out = []
    for name in STANDARD_VARS:
        value = environ.get(prefix + name)
        if value is not None:
            out.append((name, value))
    return out


def shell_quote(value):
    """Single-quote for POSIX shells ('' -> '\\'')."""
    return "'" + value.replace("'", "'\"'\"'") + "'"


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        sys.stderr.write("usage: eval \"$(python -m tools.cred_env <tenant>)\"\n")
        return 2
    tenant = argv[0]
    pairs = resolve(os.environ, tenant)
    if not pairs:
        sys.stderr.write(
            "error: no %s* credentials in the environment for tenant %r "
            "(set tenant-prefixed vars, e.g. %sZSCALER_CLIENT_ID)\n"
            % (tenant_prefix(tenant), tenant, tenant_prefix(tenant))
        )
        return 1
    for name, value in pairs:
        sys.stdout.write("export %s=%s\n" % (name, shell_quote(value)))
    sys.stderr.write(
        "resolved %d credential var(s) for tenant %r\n" % (len(pairs), tenant)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
