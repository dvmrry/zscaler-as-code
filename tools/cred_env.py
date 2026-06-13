"""Tenant-aware credential resolution: prefixed env vars -> standard names.

Pipelines hold secrets once, under tenant-scoped names in either spelling:
tenant-first (ZS2_ZIA_USERNAME, ZS2_ZSCALER_CLOUD, ...) or product-first
(ZIA_ZS2_USERNAME, ZSCALER_ZS2_CLOUD, ...) — tenant-first wins when both
are set. This prints `export` lines remapping them to the standard names
the providers and fetcher read, so the per-tenant case statement lives in
testable code instead of YAML — and NO tenant-specific value (cloud names,
vanity domains) is ever encoded in this repo; the mapping is a mechanical
transform over an allowlist.

It does NOT just dump every var it finds. It scopes export to ONE auth
mode — legacy or OneAPI, never both — because a tenant runs one or the
other (mode is uniform per tenant, not per product), and a mixed bag of
both credential sets in the environment is what crashes providers with
opaque "Plugin did not respond" errors. It also PREFLIGHTS completeness:
a product whose credentials are half-set is a loud, named failure here,
not a misleading "missing required env var" three steps downstream. A
product with no credentials at all is simply absent (a ZIA-only tenant is
fine); a product with *some but not all* is a misconfiguration.

ZCC has no legacy path — it is OneAPI-only (the provider is too) — so no
ZCC-specific variables exist; under OneAPI it shares the ZSCALER_* client.

Host overrides: each base URL the fetcher derives from a cloud name can be
pinned with an explicit env var (e.g. ZPA_LEGACY_BASE_URL), tenant-scoped
like any other. These are optional escape hatches for a cloud whose
derived host is wrong or unlisted — set one and it wins over derivation.

Usage:  eval "$(python -m tools.cred_env <tenant>)"
(Do not run under `set -x` — the export lines carry secret values. The
debug summary on stderr is secret-safe: credentials show as "set", and
tenant-identifying values — vanity domain, customer id — are hidden too
unless FETCH_DEBUG is truthy.)

Stdlib-only, Python 3.6-floor — see AGENTS.md rule 5.
"""
import os
import re
import sys

MODE_FLAG = "ZSCALER_USE_LEGACY_CLIENT"

# OneAPI: one OAuth2 client shared by every product (ZIA, ZPA, ZCC).
ONEAPI_REQUIRED = ("ZSCALER_CLIENT_ID", "ZSCALER_CLIENT_SECRET",
                   "ZSCALER_VANITY_DOMAIN")
# Exported when the required set is complete; never required themselves.
# ZPA_CUSTOMER_ID is needed only when a ZPA resource is in scope — the
# fetcher enforces that per-run, so it is optional at this layer.
ONEAPI_OPTIONAL = ("ZSCALER_CLOUD", "ZPA_CUSTOMER_ID", "ZPA_MICROTENANT_ID",
                   "ZSCALER_API_BASE_URL", "ZSCALER_LOGIN_BASE_URL")

# Legacy: per-product credential sets. Each is all-or-nothing.
LEGACY_REQUIRED = (
    ("zia", ("ZIA_USERNAME", "ZIA_PASSWORD", "ZIA_API_KEY", "ZIA_CLOUD")),
    ("zpa", ("ZPA_CLIENT_ID", "ZPA_CLIENT_SECRET", "ZPA_CUSTOMER_ID",
             "ZPA_CLOUD")),
)
LEGACY_OPTIONAL = ("ZIA_LEGACY_BASE_URL", "ZPA_LEGACY_BASE_URL")

# Distinctive legacy credentials — their presence with no mode flag set is
# a likely misconfiguration worth warning about (we still default OneAPI).
LEGACY_MARKERS = ("ZIA_USERNAME", "ZIA_PASSWORD", "ZIA_API_KEY",
                  "ZPA_CLIENT_ID", "ZPA_CLIENT_SECRET")

# Non-secret values safe to print in the debug summary; everything else
# exported is shown as "set". Clouds, hosts and base-URL overrides are
# shared-infra targeting data, not credentials and not tenant-identifying.
# Client ids and usernames are NOT here — shown as "set", conservatively.
SHOWABLE = frozenset((
    "ZIA_CLOUD", "ZPA_CLOUD", "ZSCALER_CLOUD", MODE_FLAG,
    "ZSCALER_VANITY_DOMAIN", "ZPA_CUSTOMER_ID", "ZPA_MICROTENANT_ID",
    "ZSCALER_API_BASE_URL", "ZSCALER_LOGIN_BASE_URL",
    "ZIA_LEGACY_BASE_URL", "ZPA_LEGACY_BASE_URL",
))

# Showable but tenant-IDENTIFYING: not secret, but they pin down which org/
# tenant this is. Shown as "set" unless FETCH_DEBUG is truthy, so a pipeline
# log that someone might paste into a shared channel does not leak identity.
IDENTIFYING = frozenset((
    "ZSCALER_VANITY_DOMAIN", "ZPA_CUSTOMER_ID", "ZPA_MICROTENANT_ID",
))


def _dedupe(*groups):
    seen, out = set(), []
    for group in groups:
        for name in group:
            if name not in seen:
                seen.add(name)
                out.append(name)
    return tuple(out)


# Every credential/targeting var the providers and fetcher read. Anything
# else under the prefix is ignored — the allowlist IS the contract. The
# union spans both modes; export is scoped to one mode at a time.
STANDARD_VARS = _dedupe(
    (MODE_FLAG,),
    ONEAPI_REQUIRED, ONEAPI_OPTIONAL,
    LEGACY_REQUIRED[0][1], LEGACY_REQUIRED[1][1], LEGACY_OPTIONAL,
)


def is_truthy(value):
    """Mirror the fetcher's mode flag interpretation (fetch.auth_mode_from_env)."""
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


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


def select_mode(resolved):
    """(resolved dict) -> (mode, warn). mode is 'legacy' or 'oneapi'.

    The mode flag, if set, is authoritative. If it is unset we default to
    OneAPI but set warn=True when legacy credentials are present — that
    combination almost always means the operator forgot to set the flag,
    and a silent OneAPI attempt would fail confusingly.
    """
    flag = resolved.get(MODE_FLAG)
    if flag is not None:
        return ("legacy" if is_truthy(flag) else "oneapi"), False
    warn = any(marker in resolved for marker in LEGACY_MARKERS)
    return "oneapi", warn


def _gaps(required, resolved):
    present = [v for v in required if v in resolved]
    if not present:
        return None          # product/set absent entirely — not an error
    if len(present) == len(required):
        return []            # complete
    return [v for v in required if v not in resolved]   # half-set -> gap


def scoped_export(resolved, mode):
    """(resolved dict, mode) -> (export_pairs, missing, has_creds).

    export_pairs: ordered (name, value) to emit, scoped to `mode` only —
      never a mix of the two modes' credentials — with MODE_FLAG normalized
      to 'true'/'false' and appended.
    missing: list of (group_label, [missing_vars]) for any product that is
      half-configured. A non-empty list means the caller must fail loud.
    has_creds: whether any complete credential set was found for the mode.
    """
    resolved = dict(resolved)
    out = []
    missing = []
    has_creds = False

    if mode == "oneapi":
        gaps = _gaps(ONEAPI_REQUIRED, resolved)
        if gaps:
            missing.append(("OneAPI", gaps))
        elif gaps == []:
            has_creds = True
            for name in ONEAPI_REQUIRED + ONEAPI_OPTIONAL:
                if name in resolved:
                    out.append((name, resolved[name]))
    else:  # legacy
        for product, required in LEGACY_REQUIRED:
            gaps = _gaps(required, resolved)
            if gaps:
                missing.append((product.upper(), gaps))
            elif gaps == []:
                has_creds = True
                for name in required:
                    out.append((name, resolved[name]))
        if has_creds:
            for name in LEGACY_OPTIONAL:
                if name in resolved:
                    out.append((name, resolved[name]))

    out.append((MODE_FLAG, "true" if mode == "legacy" else "false"))
    return out, missing, has_creds


def shell_quote(value):
    """Single-quote for POSIX shells ('' -> '\\'')."""
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _debug_value(name, value, verbose):
    """Secret-safe rendering for the debug summary. Credentials always show
    as "set"; tenant-identifying values show as "set" unless verbose."""
    if name not in SHOWABLE:
        return "set"
    if name in IDENTIFYING and not verbose:
        return "set"
    return value


def main(argv=None, environ=None):
    argv = argv if argv is not None else sys.argv[1:]
    environ = environ if environ is not None else os.environ
    if len(argv) != 1:
        sys.stderr.write("usage: eval \"$(python -m tools.cred_env <tenant>)\"\n")
        return 2
    tenant = argv[0]
    pairs, conflicts = resolve(environ, tenant)
    resolved = dict(pairs)
    mode, warn_legacy = select_mode(resolved)
    export_pairs, missing, has_creds = scoped_export(resolved, mode)

    if warn_legacy:
        sys.stderr.write(
            "warning: legacy credentials are set but %s is not — defaulting "
            "to OneAPI. Set %s%s=true (or the product-first spelling) if you "
            "meant legacy.\n" % (MODE_FLAG, tenant_prefix(tenant), MODE_FLAG)
        )

    if missing:
        for group, gaps in missing:
            example = candidate_names(gaps[0], tenant)
            sys.stderr.write(
                "error: %s %s credentials are incomplete — missing %s. Set "
                "the tenant-first (%s) or product-first (%s) spelling.\n"
                % (group, mode, ", ".join(gaps), example[0], example[1])
            )
        return 1

    if not has_creds:
        sys.stderr.write(
            "error: no %s credentials in the environment for tenant %r — "
            "set tenant-first vars (e.g. %sZSCALER_CLIENT_ID) or product-first "
            "vars (e.g. ZSCALER_%sCLIENT_ID)\n"
            % (mode, tenant, tenant_prefix(tenant), tenant_prefix(tenant))
        )
        return 1

    exported = set(name for name, _ in export_pairs)
    for name in conflicts:
        if name not in exported:
            continue        # conflict on a var the other mode owns — not relevant
        sys.stderr.write(
            "warning: %s is set in BOTH spellings with different values; "
            "using the tenant-first one (%s)\n"
            % (name, candidate_names(name, tenant)[0])
        )

    for name, value in export_pairs:
        sys.stdout.write("export %s=%s\n" % (name, shell_quote(value)))

    verbose = is_truthy(environ.get("FETCH_DEBUG"))
    sys.stderr.write("resolved %s credentials for tenant %r:\n" % (mode, tenant))
    hid = False
    for name, value in export_pairs:
        if name in IDENTIFYING and value and not verbose:
            hid = True
        sys.stderr.write("  %s = %s\n" % (name, _debug_value(name, value, verbose)))
    if hid:
        sys.stderr.write("  (vanity/customer-id hidden; set FETCH_DEBUG=1 to show)\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
