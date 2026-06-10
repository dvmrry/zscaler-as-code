"""Fetch detail-shaped Zscaler API JSON into pulls/<tenant>/<type>.json.

Runs with real credentials ONLY in trusted environments; here it is
exercised against fictional canned responses via an injected opener.
Stdlib-only, Python 3.6-floor. Per-resource knowledge lives in
tools/fetch_manifest.json (data); only auth/pagination patterns are code.
See AGENTS.md rules 1-5.
"""
import json
import os
import sys
import time

MANIFEST_PATH = os.path.join("tools", "fetch_manifest.json")

_manifest_cache = {}


def load_manifest():
    if not _manifest_cache:
        with open(MANIFEST_PATH) as f:
            _manifest_cache.update(json.load(f))
    return _manifest_cache


def manifest_entry(resource_type):
    manifest = load_manifest()
    if resource_type not in manifest:
        raise KeyError(
            "%r not in fetch manifest; add it to tools/fetch_manifest.json"
            % resource_type
        )
    return manifest[resource_type]


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


try:
    from urllib.parse import urlencode
except ImportError:  # pragma: no cover - py2 guard, never hit on 3.6+
    from urllib import urlencode


def _get_json(opener, url, headers, query):
    full = url + ("?" + urlencode(query) if query else "")
    status, body = opener("GET", full, headers, None)
    if status != 200:
        raise RuntimeError("GET %s returned HTTP %d" % (url, status))
    return json.loads(body.decode())


def paginate_zia(opener, url, headers, query, page_size=1000, max_pages=100000):
    """ZIA: page until a page returns fewer than page_size items.

    max_pages is a runaway guard — a real API that always returns a full
    page (total an exact multiple of page_size) would otherwise loop
    forever. The default ceiling is far above any real ZIA result set.
    """
    items = []
    page = 1
    while True:
        q = dict(query)
        q.update({"page": page, "pageSize": page_size})
        batch = _get_json(opener, url, headers, q)
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


_ONEAPI_API_BASE = "https://api.zscaler.com"
_LEGACY_ZPA_BASE = "https://config.private.zscaler.com"


def compose_url(auth_mode, product, path, ctx):
    """Compose the product base URL + resource path for the auth mode.

    ctx carries cloud/customer_id as needed. All Zscaler-specific URL
    shapes live here (SDK-derived) — confirm against dev before first work
    run; a wrong literal here is a one-line fix.
    """
    if auth_mode == "oneapi":
        if product == "zia":
            return "%s/zia/api/v1/%s" % (_ONEAPI_API_BASE, path)
        if product == "zpa":
            return "%s/zpa/mgmtconfig/v1/admin/customers/%s/%s" % (
                _ONEAPI_API_BASE, ctx["customer_id"], path
            )
    elif auth_mode == "legacy":
        if product == "zia":
            return "https://zsapi.%s.net/api/v1/%s" % (ctx["cloud"], path)
        if product == "zpa":
            return "%s/mgmtconfig/v1/admin/customers/%s/%s" % (
                _LEGACY_ZPA_BASE, ctx["customer_id"], path
            )
    raise ValueError("unknown auth_mode/product: %r/%r" % (auth_mode, product))


def build_headers(token):
    """Bearer header for OneAPI / legacy-ZPA; cookie-only (no auth header)
    for legacy-ZIA, where token is None and the session cookie rides in the
    opener's cookie jar."""
    if token is None:
        return {"Accept": "application/json"}
    return {"Authorization": "Bearer " + token, "Accept": "application/json"}


def real_opener():
    """Default opener over urllib with a cookie jar — wraps GET/POST into
    (status, bytes). The jar persists the ZIA legacy session cookie across
    calls, so legacy-ZIA GETs authenticate after the session POST without
    any explicit token. Untested here (it touches the network); the fake
    opener in tests exercises everything that consumes an opener.
    """
    import http.cookiejar
    import urllib.error
    import urllib.request

    jar = http.cookiejar.CookieJar()
    url_opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar)
    )

    def _open(method, url, headers, body):
        req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
        try:
            resp = url_opener.open(req)
            return resp.getcode(), resp.read()
        except urllib.error.HTTPError as e:  # surface status for caller
            return e.code, e.read()

    return _open


_PAGINATORS = {"zia": paginate_zia, "zpa": paginate_zpa}


def fetch_resource(resource_type, auth_mode, ctx, token, opener):
    """List one resource type into a list of detail-shaped dicts."""
    entry = manifest_entry(resource_type)
    product = entry["product"]
    url = compose_url(auth_mode, product, entry["path"], ctx)
    headers = build_headers(token)
    query = entry.get("query") or {}
    paginate = _PAGINATORS[entry.get("pagination", product)]
    return paginate(opener, url, headers, query)


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
            "audience": _ONEAPI_API_BASE,
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
    raise SystemExit("unknown auth mode %r" % auth_mode)


def products_in_manifest():
    return sorted({e["product"] for e in load_manifest().values()})


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        sys.stderr.write("usage: python -m tools.fetch <tenant>\n")
        return 2
    tenant = argv[0]
    env = os.environ
    auth_mode = auth_mode_from_env(env)
    opener = real_opener()
    ctx = {
        "cloud": env.get("ZIA_CLOUD", "") or env.get("ZSCALER_CLOUD", ""),
        "customer_id": _require(env, "ZPA_CUSTOMER_ID"),
    }
    tokens = {}
    for product in products_in_manifest():
        tokens[product] = acquire_token(auth_mode, product, env, ctx, opener)
    out_dir = os.path.join("pulls", tenant)
    os.makedirs(out_dir, exist_ok=True)
    for resource_type in sorted(load_manifest()):
        product = manifest_entry(resource_type)["product"]
        items = fetch_resource(
            resource_type, auth_mode, ctx, tokens[product], opener
        )
        path = os.path.join(out_dir, resource_type + ".json")
        with open(path, "w") as f:
            json.dump(items, f, indent=2, sort_keys=True)
            f.write("\n")
        sys.stderr.write("wrote %s (%d items)\n" % (path, len(items)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
