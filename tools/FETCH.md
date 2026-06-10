# Fetcher (`tools/fetch.py`)

Pulls detail-shaped Zscaler API JSON into `pulls/<tenant>/<type>.json`, the
input to `make transform`. Runs with real credentials only in trusted
environments; in this repo it is exercised against fictional canned
responses. Per-resource endpoints are data in `tools/fetch_manifest.json`;
adding a resource under an existing product is a manifest entry, no code.

## Pipeline

    make fetch TENANT=<tenant>                 # pulls/<tenant>/*.json
    make transform IN=pulls/<tenant> TENANT=<tenant>

The `make fetch` command reads credentials from the environment. These are
the same variables the Zscaler Terraform providers read, so existing
provider env config works unchanged.

## Auth modes

The fetcher resolves mode from `ZSCALER_USE_LEGACY_CLIENT`. Set it to a
truthy value (`1`, `true`, `yes`, `on`) for legacy mode; leave it unset or
set it to a falsey value for OneAPI mode (the default).

**OneAPI** (default; `ZSCALER_USE_LEGACY_CLIENT` unset or falsey) —
one OAuth2 bearer for both products. Note the gateway actually dialed is
`api.zsapi.net` (`api.<cloud>.zsapi.net` off production); the similar
`api.zscaler.com` is only the OAuth audience string and serves no valid
certificate — attempts to call it fail TLS verification on any network.

    ZSCALER_CLIENT_ID
    ZSCALER_CLIENT_SECRET
    ZSCALER_VANITY_DOMAIN    vanity domain (token host)
    ZSCALER_CLOUD            cloud suffix (empty for production)
    ZPA_CUSTOMER_ID          ZPA customer id

**Legacy** (`ZSCALER_USE_LEGACY_CLIENT=true`) — per-product:

    ZIA_API_KEY              obfuscated per request; session cookie auth
    ZIA_USERNAME
    ZIA_PASSWORD
    ZIA_CLOUD                e.g. zscalertwo (ZIA host)
    ZPA_CLIENT_ID            /signin client-credentials
    ZPA_CLIENT_SECRET
    ZPA_CUSTOMER_ID

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

Validate against the dev tenant (OneAPI) first, then production (legacy).
