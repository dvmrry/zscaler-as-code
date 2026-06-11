"""Fetch detail-shaped Zscaler API JSON into pulls/<tenant>/<type>.json.

Runs with real credentials ONLY in trusted environments; here it is
exercised against fictional canned responses via an injected opener.
Stdlib-only, Python 3.6-floor. Per-resource knowledge lives in
tools/registry.json (data); only auth/pagination patterns are code.
See AGENTS.md rules 1-5.
"""
import json
import os
import sys
import time

def load_manifest():
    from tools.registry import load_registry
    out = {}
    for rt, e in load_registry().items():
        if "fetch" in e:
            entry = dict(e["fetch"])
            entry["product"] = e["product"]
            out[rt] = entry
    return out


def manifest_entry(resource_type):
    from tools.registry import fetch_entry
    return fetch_entry(resource_type)


def obfuscate_api_key(api_key, timestamp):
    """Port of the ZIA legacy key-obfuscation algorithm (public SDK).

    timestamp is milliseconds-since-epoch as a string. Raises ValueError
    on inputs too short to index, matching the SDK guard.
    """
    if len(timestamp) < 6 or len(api_key) < 12:
        raise ValueError("timestamp or api key below required length")
    high = timestamp[-6:]
    low = "%06d" % (int(high) >> 1)
    obfuscated = ""
    for ch in high:
        obfuscated += api_key[int(ch)]
    for ch in low:
        obfuscated += api_key[int(ch) + 2]
    return obfuscated


from urllib.parse import quote as _quote, urlencode


def _get_json(opener, url, headers, query):
    full = url + ("?" + urlencode(query) if query else "")
    status, body = opener("GET", full, headers, None)
    if status != 200:
        raise RuntimeError("GET %s returned HTTP %d" % (url, status))
    return json.loads(body.decode())


def paginate_zia(opener, url, headers, query, page_size=1000, max_pages=100000,
                 envelope=None):
    """ZIA-style: page until a page returns fewer than page_size items.

    max_pages is a runaway guard — a real API that always returns a full
    page (total an exact multiple of page_size) would otherwise loop
    forever. The default ceiling is far above any real ZIA result set.
    envelope: some endpoints wrap the page in an object (e.g. ZCC v1
    trusted networks: {"totalCount": N, "trustedNetworkContracts": [...]})
    — name the wrapping key in the registry entry to unwrap it.
    """
    items = []
    page = 1
    while True:
        q = dict(query)
        q.update({"page": page, "pageSize": page_size})
        batch = _get_json(opener, url, headers, q)
        if envelope is not None and isinstance(batch, dict):
            batch = batch.get(envelope) or []
        if not isinstance(batch, list):
            raise RuntimeError("ZIA %s did not return a list page" % url)
        items.extend(batch)
        if len(batch) < page_size:
            return items
        if page >= max_pages:
            raise RuntimeError(
                "ZIA %s exceeded max_pages=%d; aborting runaway pagination"
                % (url, max_pages)
            )
        page += 1


def paginate_zpa(opener, url, headers, query, page_size=500):
    """ZPA: page up to totalPages, collecting the `list` field."""
    items = []
    page = 1
    while True:
        q = dict(query)
        q.update({"page": page, "pagesize": page_size})
        payload = _get_json(opener, url, headers, q)
        items.extend(payload.get("list") or [])
        total = int(payload.get("totalPages", 1) or 1)
        if page >= total:
            return items
        page += 1


def paginate_single(opener, url, headers, query):
    """Single-object endpoints (no pagination): GET once, return as a
    one-element list so the caller can iterate items uniformly.

    Used for ZCC singleton resources (fail-open policy, web-privacy) that
    return a plain JSON object rather than a paged array.
    """
    payload = _get_json(opener, url, headers, query)
    if isinstance(payload, list):
        return payload
    return [payload]


def paginate_zcc_v2(opener, url, headers, query, per_page=100, max_pages=100000):
    """ZCC v2 offset-based pagination: {items, total, offset, limit, count}.

    Advances skip by per_page after each page. Terminates on any of:
    - count == 0 or items empty (empty-page safety)
    - count < limit (short last page)
    - collected >= total (server-authoritative total)
    """
    items = []
    skip = 0
    page = 0
    while True:
        q = dict(query)
        q.update({"skip": skip, "perPage": per_page})
        payload = _get_json(opener, url, headers, q)
        page_items = payload.get("items") or []
        items.extend(page_items)
        count = payload.get("count", 0)
        total = payload.get("total", 0)
        limit = payload.get("limit", per_page)
        if count == 0 or not page_items:
            break
        if limit > 0 and count < limit:
            break
        if total > 0 and len(items) >= total:
            break
        page += 1
        if page >= max_pages:
            raise RuntimeError(
                "ZCC v2 %s exceeded max_pages=%d; aborting runaway pagination"
                % (url, max_pages)
            )
        skip += per_page
    return items


# The OAuth audience is NOT a dialable host — api.zscaler.com serves no
# valid cert and exists only as the token-request audience value. The real
# OneAPI gateway is api.zsapi.net (api.<cloud>.zsapi.net off production).
_ONEAPI_AUDIENCE = "https://api.zscaler.com"
_LEGACY_ZPA_BASE = "https://config.private.zscaler.com"


def _oneapi_gateway(cloud):
    norm = (cloud or "").strip().lower()
    if norm in ("", "production"):
        return "https://api.zsapi.net"
    return "https://api.%s.zsapi.net" % norm


def _legacy_zcc_base(cloud):
    """ZCC legacy base URL (SDK-derived): https://api-mobile.<cloud>.net/papi"""
    return "https://api-mobile.%s.net/papi" % cloud


def compose_url(auth_mode, product, path, ctx):
    """Compose the product base URL + resource path for the auth mode.

    ctx carries cloud/customer_id as needed. All Zscaler-specific URL
    shapes live here (SDK-derived) — confirm against dev before first work
    run; a wrong literal here is a one-line fix.

    ZCC paths in the registry carry their full post-gateway path including
    the /zcc/papi/public/v1/ prefix (e.g. zcc/papi/public/v1/webForwardingProfile/listByCompany).
    For OneAPI, the gateway is the same api.zsapi.net as zia/zpa.
    For legacy, the base is https://api-mobile.<cloud>.net/papi (SDK v2_config.go).
    """
    if auth_mode == "oneapi":
        if product == "zia":
            return "%s/zia/api/v1/%s" % (_oneapi_gateway(ctx.get("cloud", "")), path)
        if product == "zpa":
            return "%s/zpa/mgmtconfig/v1/admin/customers/%s/%s" % (
                _oneapi_gateway(ctx.get("cloud", "")), ctx["customer_id"], path
            )
        if product == "zcc":
            return "%s/%s" % (_oneapi_gateway(ctx.get("cloud", "")), path)
    elif auth_mode == "legacy":
        if product == "zia":
            return "https://zsapi.%s.net/api/v1/%s" % (ctx["cloud"], path)
        if product == "zpa":
            return "%s/mgmtconfig/v1/admin/customers/%s/%s" % (
                _LEGACY_ZPA_BASE, ctx["customer_id"], path
            )
        if product == "zcc":
            return "%s/%s" % (_legacy_zcc_base(ctx["zcc_cloud"]), path)
    raise ValueError("unknown auth_mode/product: %r/%r" % (auth_mode, product))


def build_headers(token, auth_token_header=False):
    """Bearer header for OneAPI / legacy-ZPA; cookie-only (no auth header)
    for legacy-ZIA, where token is None and the session cookie rides in the
    opener's cookie jar.

    auth_token_header=True uses the ZCC legacy header name 'auth-token'
    (SDK: v2_client.go req.Header.Set("auth-token", ...)) instead of
    the standard Authorization: Bearer form.
    """
    if token is None:
        return {"Accept": "application/json"}
    if auth_token_header:
        return {"auth-token": token, "Accept": "application/json"}
    return {"Authorization": "Bearer " + token, "Accept": "application/json"}


def ca_bundle_path(env):
    """Path to a CA bundle that trusts the corporate TLS-inspection root,
    or None to use the system defaults.

    Zscaler (and most enterprise proxies) MITM-inspect outbound HTTPS —
    including, ironically, traffic to the Zscaler API itself — presenting a
    corporate root CA that Python does not trust out of the box, so the
    handshake fails. Point one of the de-facto-standard vars at the
    exported root (the same ones curl/requests honor); no new var invented.
    """
    return env.get("REQUESTS_CA_BUNDLE") or env.get("SSL_CERT_FILE") or None


def connection_hint(reason):
    """One-line remediation for common connection failures, so the error is
    actionable where it happens instead of requiring a relayed traceback."""
    text = reason.lower()
    if "certificate" in text or "ssl" in text:
        return (
            "hint: corporate TLS inspection? set REQUESTS_CA_BUNDLE to the "
            "exported proxy root CA (it is ADDED to system trust)"
        )
    if (
        "refused" in text
        or "timed out" in text
        or "unreachable" in text
        or "nodename" in text
        or "name or service" in text
    ):
        return (
            "hint: blocked egress? if an explicit proxy is required set "
            "HTTPS_PROXY (and NO_PROXY); transparent agents need nothing"
        )
    return "hint: see tools/FETCH.md (proxy and TLS notes)"


def real_opener(env=None):
    """Default opener over urllib with a cookie jar — wraps GET/POST into
    (status, bytes). The jar persists the ZIA legacy session cookie across
    calls, so legacy-ZIA GETs authenticate after the session POST without
    any explicit token. If a CA bundle is configured (see ca_bundle_path),
    HTTPS verifies against it so corporate TLS inspection does not break the
    handshake. Untested here (it touches the network); the fake opener in
    tests exercises everything that consumes an opener.
    """
    import http.cookiejar
    import ssl
    import urllib.error
    import urllib.request

    if env is None:
        env = os.environ
    jar = http.cookiejar.CookieJar()
    handlers = [urllib.request.HTTPCookieProcessor(jar)]
    bundle = ca_bundle_path(env)
    if bundle:
        # ADD the corporate root on top of system trust (not instead of it):
        # hosts the proxy bypasses from inspection present their real public
        # certs and must still verify. build_opener keeps the default
        # ProxyHandler, so HTTPS_PROXY/NO_PROXY env vars are honored.
        context = ssl.create_default_context()
        context.load_verify_locations(cafile=bundle)
        handlers.append(urllib.request.HTTPSHandler(context=context))
    url_opener = urllib.request.build_opener(*handlers)

    def _open(method, url, headers, body):
        req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
        try:
            resp = url_opener.open(req)
            return resp.getcode(), resp.read()
        except urllib.error.HTTPError as e:  # surface status for caller
            return e.code, e.read()
        except urllib.error.URLError as e:
            # Self-explanatory on this side — no traceback relay needed.
            raise SystemExit(
                "cannot reach %s: %s\n%s"
                % (url.split("?")[0], e.reason, connection_hint(str(e.reason)))
            )

    return _open


_PAGINATORS = {
    "zia": paginate_zia,
    "zpa": paginate_zpa,
    "single": paginate_single,
    "zcc_v2": paginate_zcc_v2,
}


def expand_paths(entry):
    """List of concrete API paths for a fetch entry. Entries may declare
    {"expand": {"placeholder": [values]}} with "{placeholder}" in path —
    per-type APIs like webApplicationRules/{rule_type}. One placeholder
    max (no product needs more)."""
    path = entry["path"]
    expand = entry.get("expand") or {}
    if not expand:
        return [path]
    if len(expand) != 1:
        raise ValueError("expand supports exactly one placeholder: %r" % sorted(expand))
    key = sorted(expand)[0]
    token = "{%s}" % key
    if token not in path:
        raise ValueError("expand key %r not present in path %r" % (key, path))
    return [path.replace(token, _quote(value, safe="")) for value in expand[key]]


def _fetch_paths(entry, auth_mode, ctx, token, opener):
    product = entry["product"]
    # ZCC legacy auth uses a non-standard header: auth-token (SDK v2_client.go)
    use_auth_token_header = (product == "zcc" and auth_mode == "legacy")
    headers = build_headers(token, auth_token_header=use_auth_token_header)
    query = entry.get("query") or {}
    paginate = _PAGINATORS[entry.get("pagination", product)]
    kwargs = {}
    if entry.get("envelope") and paginate is paginate_zia:
        kwargs["envelope"] = entry["envelope"]
    items = []
    for path in expand_paths(entry):
        url = compose_url(auth_mode, product, path, ctx)
        items.extend(paginate(opener, url, headers, query, **kwargs))
    return items


def fetch_resource(resource_type, auth_mode, ctx, token, opener):
    """List one resource type into a list of detail-shaped dicts."""
    return _fetch_paths(manifest_entry(resource_type), auth_mode, ctx, token, opener)


def _require(env, name):
    value = env.get(name)
    if not value:
        raise SystemExit("missing required env var %s" % name)
    return value


def auth_mode_from_env(env):
    """oneapi unless ZSCALER_USE_LEGACY_CLIENT is truthy."""
    flag = (env.get("ZSCALER_USE_LEGACY_CLIENT") or "").strip().lower()
    return "legacy" if flag in ("1", "true", "yes", "on") else "oneapi"


def _zslogin_host(vanity, cloud):
    """OneAPI token host. Production (empty/PRODUCTION cloud) has no suffix;
    other clouds lowercase into the host, per the SDK."""
    norm = (cloud or "").strip().lower()
    suffix = "" if norm in ("", "production") else norm
    return "https://%s.zslogin%s.net" % (vanity, suffix)


def acquire_token(auth_mode, product, env, ctx, opener, now_ms=None):
    """Acquire auth for one product. Returns a bearer token string, or None
    for legacy-ZIA (cookie-based; the cookie lives in the opener).

    env is a dict (os.environ at the call site) so tests stay hermetic.
    """
    if auth_mode == "oneapi":
        token_url = _zslogin_host(
            _require(env, "ZSCALER_VANITY_DOMAIN"), env.get("ZSCALER_CLOUD", "")
        ) + "/oauth2/v1/token"
        body = urlencode({
            "grant_type": "client_credentials",
            "client_id": _require(env, "ZSCALER_CLIENT_ID"),
            "client_secret": _require(env, "ZSCALER_CLIENT_SECRET"),
            "audience": _ONEAPI_AUDIENCE,
        }).encode()
        status, raw = opener(
            "POST", token_url,
            {"Content-Type": "application/x-www-form-urlencoded"}, body,
        )
        if status != 200:
            raise SystemExit("OneAPI token request failed: HTTP %d" % status)
        return json.loads(raw.decode())["access_token"]

    if auth_mode == "legacy":
        if product == "zpa":
            url = "%s/signin" % _LEGACY_ZPA_BASE
            body = urlencode({
                "client_id": _require(env, "ZPA_CLIENT_ID"),
                "client_secret": _require(env, "ZPA_CLIENT_SECRET"),
            }).encode()
            status, raw = opener(
                "POST", url,
                {"Content-Type": "application/x-www-form-urlencoded"}, body,
            )
            if status != 200:
                raise SystemExit("ZPA signin failed: HTTP %d" % status)
            return json.loads(raw.decode())["access_token"]
        if product == "zia":
            ts = str(now_ms if now_ms is not None else int(time.time() * 1000))
            url = "https://zsapi.%s.net/api/v1/authenticatedSession" % ctx["cloud"]
            payload = json.dumps({
                "apiKey": obfuscate_api_key(_require(env, "ZIA_API_KEY"), ts),
                "username": _require(env, "ZIA_USERNAME"),
                "password": _require(env, "ZIA_PASSWORD"),
                "timestamp": ts,
            }).encode()
            status, raw = opener(
                "POST", url, {"Content-Type": "application/json"}, payload
            )
            if status != 200:
                raise SystemExit("ZIA session auth failed: HTTP %d" % status)
            return None
        if product == "zcc":
            # ZCC legacy: POST /auth/v1/login with {apiKey, secretKey} ->
            # {jwtToken} used as auth-token header (SDK zcc/v2_client.go).
            cloud = _require(env, "ZCC_CLOUD")
            url = "%s/auth/v1/login" % _legacy_zcc_base(cloud)
            payload = json.dumps({
                "apiKey": _require(env, "ZCC_CLIENT_ID"),
                "secretKey": _require(env, "ZCC_CLIENT_SECRET"),
            }).encode()
            status, raw = opener(
                "POST", url, {"Content-Type": "application/json"}, payload
            )
            if status != 200:
                raise SystemExit("ZCC login failed: HTTP %d" % status)
            return json.loads(raw.decode())["jwtToken"]
    raise SystemExit("unknown auth mode %r" % auth_mode)


def products_in_manifest():
    return sorted({e["product"] for e in load_manifest().values()})


def diag_hosts(env):
    """Unique HTTPS hosts the fetcher will contact in the configured mode."""
    if auth_mode_from_env(env) == "oneapi":
        vanity = env.get("ZSCALER_VANITY_DOMAIN") or "<vanity>"
        cloud = env.get("ZSCALER_CLOUD", "")
        login = _zslogin_host(vanity, cloud)
        gateway = _oneapi_gateway(cloud)
        return sorted({login.split("//", 1)[1], gateway.split("//", 1)[1]})
    cloud = env.get("ZIA_CLOUD", "") or env.get("ZSCALER_CLOUD", "") or "<cloud>"
    zcc_cloud = env.get("ZCC_CLOUD", "") or cloud
    # ZCC legacy base: api-mobile.<cloud>.net (path /papi is not a hostname)
    zcc_host = "api-mobile.%s.net" % zcc_cloud
    return sorted({
        "zsapi.%s.net" % cloud,
        "config.private.zscaler.com",
        zcc_host,
    })


def _try_tls(host, context):
    """(ok, detail) for an HTTPS request to host under context.

    Goes through urllib — the same stack the fetcher uses — so it is
    proxy-aware (HTTPS_PROXY/system proxy) exactly like production. A raw
    socket probe would bypass the proxy and hang on networks that block
    direct egress, diagnosing a problem the fetcher does not have. Any
    HTTP status (even 401/403) means TLS succeeded.
    """
    import urllib.error
    import urllib.request
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=context)
    )
    try:
        resp = opener.open("https://%s/" % host, timeout=15)
        return True, "HTTP %d" % resp.getcode()
    except urllib.error.HTTPError as e:
        return True, "HTTP %d" % e.code
    except urllib.error.URLError as e:
        return False, str(e.reason)
    except OSError as e:
        return False, str(e)


def run_diag(env):
    """Per host, try system trust then system+bundle; print which leg works.

    Output is infrastructure-only (host, verify result, issuer org) —
    designed so the result can be acted on, or relayed, without exposing
    anything tenant-specific.
    """
    import ssl
    bundle = ca_bundle_path(env)
    system_ctx = ssl.create_default_context()
    bundle_ctx = None
    if bundle:
        bundle_ctx = ssl.create_default_context()
        bundle_ctx.load_verify_locations(cafile=bundle)
    for host in diag_hosts(env):
        if "<" in host:
            sys.stderr.write("%s: skipped (env vars not set)\n" % host)
            continue
        ok, detail = _try_tls(host, system_ctx)
        line = "%s: system-trust %s (%s)" % (host, "OK" if ok else "FAIL", detail)
        if bundle_ctx is not None:
            ok2, detail2 = _try_tls(host, bundle_ctx)
            line += "; +bundle %s (%s)" % ("OK" if ok2 else "FAIL", detail2)
        else:
            line += "; no CA bundle configured (set REQUESTS_CA_BUNDLE)"
        sys.stderr.write(line + "\n")
    return 0


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if argv == ["--diag"]:
        return run_diag(os.environ)
    if len(argv) < 1:
        sys.stderr.write(
            "usage: python -m tools.fetch <tenant> [resource_type ...] | --diag\n"
        )
        return 2
    tenant = argv[0]
    only = set(argv[1:]) or None
    if only:
        unknown = only - set(load_manifest())
        if unknown:
            sys.stderr.write(
                "error: unknown resource type(s): %s\nvalid: %s\n"
                % (", ".join(sorted(unknown)), ", ".join(sorted(load_manifest())))
            )
            return 2
    env = os.environ
    auth_mode = auth_mode_from_env(env)
    opener = real_opener()
    ctx = {
        "cloud": env.get("ZIA_CLOUD", "") or env.get("ZSCALER_CLOUD", ""),
        "customer_id": _require(env, "ZPA_CUSTOMER_ID"),
        "zcc_cloud": env.get("ZCC_CLOUD", ""),
    }
    out_dir = os.path.join("pulls", tenant)
    return fetch_all(auth_mode, env, ctx, opener, out_dir, only=only)


def fetch_all(auth_mode, env, ctx, opener, out_dir, only=None):
    """Fetch every registered resource, completing what it can.

    One product's failure (missing entitlement, wrong path, outage) must
    not block the others — failures are collected and summarized at the
    end, and the exit code is non-zero when anything failed. Learned the
    hard way: zcc sorts first, so a single 404 used to abort all the
    healthy pulls behind it.

    only: optional set of resource types to fetch (scoped drift — e.g.
    an hourly URL-categories check shouldn't pull all 16 resources).
    Tokens are acquired only for products actually needed.
    """
    wanted = sorted(only) if only else sorted(load_manifest())
    needed_products = set(manifest_entry(rt)["product"] for rt in wanted)
    tokens = {}
    failed_products = {}
    for product in products_in_manifest():
        if product not in needed_products:
            continue
        try:
            tokens[product] = acquire_token(auth_mode, product, env, ctx, opener)
        except SystemExit as e:
            failed_products[product] = str(e)
    os.makedirs(out_dir, exist_ok=True)
    failures = {}
    for resource_type in wanted:
        product = manifest_entry(resource_type)["product"]
        if product in failed_products:
            failures[resource_type] = "auth failed: %s" % failed_products[product]
            continue
        try:
            items = fetch_resource(
                resource_type, auth_mode, ctx, tokens[product], opener
            )
        except (RuntimeError, SystemExit, ValueError) as e:
            failures[resource_type] = str(e)
            continue
        path = os.path.join(out_dir, resource_type + ".json")
        with open(path, "w") as f:
            json.dump(items, f, indent=2, sort_keys=True)
            f.write("\n")
        sys.stderr.write("wrote %s (%d items)\n" % (path, len(items)))
    if failures:
        sys.stderr.write("\n%d resource(s) FAILED:\n" % len(failures))
        for resource_type in sorted(failures):
            sys.stderr.write("  %s: %s\n" % (resource_type, failures[resource_type]))
        sys.stderr.write(
            "hint: a 404 on ONE endpoint means that path/version is not "
            "mounted on the gateway for your cloud (try the v1 equivalent "
            "in the registry); 404s on EVERY endpoint of a product mean "
            "the API client lacks that product's entitlement (Zidentity "
            "console). Successful pulls above are unaffected either way.\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
