# Fetcher (`tools/fetch.py`)

Pulls detail-shaped Zscaler API JSON into `pulls/<tenant>/<type>.json`, the
input to `make transform`. Runs with real credentials only in trusted
environments; in this repo it is exercised against fictional canned
responses. Per-resource endpoints are data in `tools/fetch_manifest.json`;
adding a resource under an existing product is a manifest entry, no code.

## Pipeline

    make fetch TENANT=<tenant>                 # pulls/<tenant>/*.json
    make transform IN=pulls/<tenant> TENANT=<tenant>

## Auth modes (set ZS_AUTH)

Both modes are wired. The Zscaler-specific URL/auth literals in
`compose_url` and `acquire_token` are SDK-derived — confirm them against
the dev tenant on the first run; a wrong literal is a one-line fix.

**OneAPI** (`ZS_AUTH=oneapi`) — one OAuth2 bearer for both products:

    ZS_VANITY            vanity domain (token host)
    ZS_CLOUD             cloud suffix (empty for production)
    ZS_CLIENT_ID
    ZS_CLIENT_SECRET
    ZS_ZPA_CUSTOMER_ID   ZPA customer id

**Legacy** (`ZS_AUTH=legacy`) — per-product:

    ZS_CLOUD             e.g. zscalertwo (ZIA host + ZPA paths)
    ZS_ZIA_API_KEY       obfuscated per request; session cookie auth
    ZS_ZIA_USERNAME
    ZS_ZIA_PASSWORD
    ZS_ZPA_CLIENT_ID     /signin client-credentials
    ZS_ZPA_CLIENT_SECRET
    ZS_ZPA_CUSTOMER_ID

Credentials are read from the environment at runtime only. They are never
written to disk, never logged, and never enter `pulls/` output. Real pulls
under `pulls/<tenant>/` are gitignored and must never be committed.

## Validation order

Validate against the dev tenant (OneAPI) first, then production (legacy).
