# Fetcher (`tools/fetch.py`)

Pulls detail-shaped Zscaler API JSON into `pulls/<tenant>/<type>.json`, the
input to `make transform`. Runs with real credentials only in trusted
environments; in this repo it is exercised against fictional canned
responses. Per-resource endpoints are data in `tools/registry.json`;
adding a resource under an existing product is a registry entry, no code.

## Pipeline

    make fetch TENANT=<tenant>                 # pulls/<tenant>/*.json
    make transform IN=pulls/<tenant> TENANT=<tenant>

The `make fetch` command reads credentials from the environment. These are
the same variables the Zscaler Terraform providers read, so existing
provider env config works unchanged.

## Scoped fetch

    make fetch TENANT=<tenant> RESOURCE=<type>          # one resource type
    make fetch TENANT=<tenant> RESOURCE="zia zpa"       # whole product(s)
    make drift TENANT=<tenant> RESOURCE="zia zpa"       # scoped drift

`RESOURCE` accepts resource types AND product tokens (`zia`/`zpa`/`zcc`,
expanded from the registry), space-separated. This is the mechanism behind
scoped drift checks (an hourly `zia_url_categories` pull) and behind
DISABLING a product operationally: a pipeline that should ignore ZCC until
OneAPI is enabled sets `RESOURCE="zia zpa"` — fetch never contacts ZCC, no
ZCC credentials are needed, and drift stays green and meaningful. Do NOT
disable a product by deleting its credentials: the fetch failure aborts
drift before transform and every run goes red. Tokens are acquired only
for the products actually in scope.

Env vars are required per product in scope, not unconditionally:

- `ZPA_CUSTOMER_ID` is required only when a `zpa_*` resource is in scope (or
  for an unscoped full fetch). A `RESOURCE=zia_*` or `RESOURCE=zcc_*` fetch
  no longer demands it.
- The auth env vars for the in-scope product's mode (OneAPI `ZSCALER_*`, or
  the legacy per-product set below) are still required — a token is acquired
  for that product before any resource is pulled.

Limitations:

- A single HTTP 404 on any one expand path (e.g. one of the
  `zia_cloud_app_control_rule` rule-type paths) marks the entire resource
  type failed and exits non-zero; other resource types are unaffected.
- A scoped run writes only the named resource file; previously fetched files
  for other types are left as-is.

## Diagnosing connectivity (`make fetch-diag`)

    make fetch-diag    # probe TLS to the fetcher's hosts; print issuer

`make fetch-diag` (the `--diag` mode of `tools.fetch`) probes TLS to the
hosts the fetcher will contact in the configured auth mode, under system
trust and then with any configured CA bundle, and prints the verify result
per host. Run it to diagnose CA-bundle or proxy configuration before a real
fetch — it touches no credentials and writes nothing tenant-specific.

Cloud-app-control rules fetch per rule type via the registry's `expand`
list — trim or extend the types to what your tenant uses. Rule types your
tenant is not entitled to may return HTTP 404 (not a 200 empty list), and a
404 on any one expand path fails the entire `zia_cloud_app_control_rule`
pull (other resource types are unaffected). Trim the `expand` list in
`tools/registry.json` to only the rule types your tenant is entitled to.

## Auth modes

The fetcher resolves mode from `ZSCALER_USE_LEGACY_CLIENT`. Set it to a
truthy value (`1`, `true`, `yes`, `on`) for legacy mode; leave it unset or
set it to a falsey value for OneAPI mode (the default). Mode is uniform per
tenant, not per product — a tenant runs entirely legacy or entirely OneAPI,
never a mix. Only that mode's variables are required; the other mode's are
ignored.

**OneAPI** (default; `ZSCALER_USE_LEGACY_CLIENT` unset or falsey) —
one OAuth2 bearer for all products (ZIA, ZPA, ZCC); the same credential set
is used regardless of product. (The implementation currently acquires a
token once per product — up to three requests — but all three are
identical; a future cleanup could acquire it once and share it.) Note the
gateway actually dialed is `api.zsapi.net` (`api.<cloud>.zsapi.net` off
production); the similar `api.zscaler.com` is only the OAuth audience string
and serves no valid certificate — attempts to call it fail TLS verification
on any network.

    ZSCALER_CLIENT_ID        required
    ZSCALER_CLIENT_SECRET    required
    ZSCALER_VANITY_DOMAIN    required — vanity domain (token host)
    ZSCALER_CLOUD            optional — cloud suffix (empty for production)
    ZPA_CUSTOMER_ID          required only when a zpa_* resource is in scope

**Legacy** (`ZSCALER_USE_LEGACY_CLIENT=true`) — per-product. Each product's
set is all-or-nothing: a half-set product is a loud error, an absent product
is simply not fetched.

    ZIA_API_KEY              obfuscated per request; session cookie auth
    ZIA_USERNAME
    ZIA_PASSWORD
    ZIA_CLOUD                required — e.g. zscalertwo; selects the ZIA host
                             https://zsapi.<cloud>.net (a missing cloud is a
                             loud error, not a malformed host)
    ZPA_CLIENT_ID            /signin client-credentials
    ZPA_CLIENT_SECRET
    ZPA_CUSTOMER_ID
    ZPA_CLOUD                required — e.g. ZPATWO; selects the config base
                             (PRODUCTION/ZPATWO/BETA/GOV/GOVUS). The same
                             ZPA_CLOUD drives the Terraform provider's base
                             via the SDK, so fetch and provider agree.

**ZCC is OneAPI-only.** It has no legacy path here — the ZCC provider
(`0.1.0-beta.1`, pinned) is OneAPI-only too. Under OneAPI, ZCC shares the
`ZSCALER_*` credentials and the same gateway. In legacy mode a ZCC fetch
fails loud; scope it out with `RESOURCE="zia zpa"` until you migrate the
tenant to OneAPI.

### Host overrides (escape hatches)

Each base URL the fetcher derives from a cloud name can be pinned with an
explicit env var, tenant-scoped like any other credential. Set one when a
derived host is wrong or the cloud is unlisted — the override wins over
derivation, so it *restores* fetch/provider agreement rather than breaking
it. All are optional:

    ZPA_LEGACY_BASE_URL      legacy ZPA config base (e.g. https://config.zpatwo.net)
    ZIA_LEGACY_BASE_URL      legacy ZIA base (e.g. https://zsapi.zscalertwo.net)
    ZSCALER_API_BASE_URL     OneAPI gateway base
    ZSCALER_LOGIN_BASE_URL   OneAPI token host

### Startup debug

Every `make fetch` prints a secret-safe summary to stderr first: the auth
mode, whether a proxy is set (never its value), the safe targeting vars
(clouds, vanity domain, customer id), and the actual base URLs/hosts it will
dial for the in-scope products. Credentials are never printed. Use it to
confirm a run is pointed where you expect before it authenticates.

Credentials are read from the environment at runtime only. They are never
written to disk, never logged, and never enter `pulls/` output. Real pulls
under `pulls/<tenant>/` are gitignored and must never be committed.

## Corporate TLS inspection (Zscaler proxy)

Enterprise proxies — Zscaler's included — MITM-inspect outbound HTTPS,
presenting a corporate root CA that Python does not trust by default, so
the fetcher's TLS handshake fails (ironically, even when reaching the
Zscaler API itself). Point the fetcher at the exported inspection root via
either standard variable (same ones curl and requests honor):

    REQUESTS_CA_BUNDLE=/path/to/corp-root-ca.pem    # preferred
    SSL_CERT_FILE=/path/to/corp-root-ca.pem          # fallback

The bundle is ADDED on top of system trust, not substituted for it — hosts
the proxy bypasses from inspection (auth endpoints often are) present real
public certs and still verify. No verification bypass is provided by
design — trust the corporate root explicitly.

Proxy routing:

- **Transparent interception** (Zscaler Client Connector tunnel): nothing
  to configure; only the CA bundle above matters.
- **Explicit proxy**: set the usual `HTTPS_PROXY` (and `NO_PROXY`)
  variables — urllib honors them, and macOS system proxy settings,
  automatically. PAC files are not evaluated by Python: if your network
  uses one, set `HTTPS_PROXY` to the proxy host it resolves to.

Connection failures print a one-line cause and a remediation hint
(certificate → CA bundle; refused/timeout → proxy), so the fix is visible
where the error happens.

## Validation order

Validate with a non-production tenant first; both auth modes work with any tenant.
