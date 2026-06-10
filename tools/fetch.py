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


def paginate_zia(opener, url, headers, query, page_size=1000):
    """ZIA: page until a page returns fewer than page_size items."""
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
