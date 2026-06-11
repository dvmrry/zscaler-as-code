"""Tests for tools/fetch.py. All canned responses are fictional."""
import io
import json
import os
import sys
import unittest

from tools.fetch import load_manifest, manifest_entry, obfuscate_api_key, paginate_zia, paginate_zpa, paginate_single, paginate_zcc_v2, build_headers, compose_url, fetch_resource, acquire_token, products_in_manifest, auth_mode_from_env, _zslogin_host, _legacy_zcc_base, ca_bundle_path, connection_hint, diag_hosts, expand_paths


class ManifestTest(unittest.TestCase):
    def test_known_entry(self):
        e = manifest_entry("zpa_segment_group")
        self.assertEqual(e["product"], "zpa")
        self.assertIn("path", e)

    def test_unknown_entry_raises(self):
        with self.assertRaises(KeyError):
            manifest_entry("zia_no_such_resource")

    def test_manifest_products_valid(self):
        for rt, e in load_manifest().items():
            self.assertIn(e["product"], ("zcc", "zia", "zpa"), rt)
            self.assertIn("path", e)


class ObfuscateTest(unittest.TestCase):
    def test_known_vector(self):
        # Fictional key/timestamp; output computed from the published algorithm.
        self.assertEqual(
            obfuscate_api_key("abcdefghijklmnop", "1700000000"),
            _expected_obfuscation("abcdefghijklmnop", "1700000000"),
        )

    def test_rejects_short_inputs(self):
        with self.assertRaises(ValueError):
            obfuscate_api_key("short", "12345")

    def test_varied_timestamp_vector(self):
        # Hardcoded expected output (not mirror-derived): timestamp with
        # varied trailing digits exercises 8 distinct key positions.
        self.assertEqual(
            obfuscate_api_key("abcdefghijklmnop", "1699987654"), "jihgfeglfkej"
        )


def _expected_obfuscation(api_key, ts):
    # Reference re-implementation, identical to obfuscate_api_key, used to
    # pin behavior without embedding a magic string. The live dev tenant is
    # the real confirmation (see plan Task 6).
    high = ts[-6:]
    low = "%06d" % (int(high) >> 1)
    out = ""
    for ch in high:
        out += api_key[int(ch)]
    for ch in low:
        out += api_key[int(ch) + 2]
    return out


class FakeOpener:
    """Maps url (ignoring query) -> list of (status, json-able) responses by
    call order. Records urls and request bodies for assertions. Used by the
    paginator, dispatch, and token-acquisition tests."""
    def __init__(self, pages_by_path):
        self.pages_by_path = pages_by_path
        self.calls = []
        self.bodies = []

    def __call__(self, method, url, headers, body):
        self.calls.append(url)
        self.bodies.append(body)
        path = url.split("?")[0]
        queue = self.pages_by_path[path]
        status, payload = queue.pop(0)
        return status, json.dumps(payload).encode()


class PaginateZiaTest(unittest.TestCase):
    def test_stops_on_short_page(self):
        opener = FakeOpener({
            "https://x/urlCategories": [
                (200, [{"id": "1"}, {"id": "2"}]),
                (200, [{"id": "3"}]),
            ]
        })
        out = paginate_zia(opener, "https://x/urlCategories", {}, {}, page_size=2)
        self.assertEqual([i["id"] for i in out], ["1", "2", "3"])
        self.assertEqual(len(opener.calls), 2)

    def test_empty_first_page(self):
        opener = FakeOpener({"https://x/u": [(200, [])]})
        self.assertEqual(paginate_zia(opener, "https://x/u", {}, {}, page_size=500), [])

    def test_caps_runaway_pagination(self):
        # An API that always returns a full page must not loop forever.
        full = [{"id": str(n)} for n in range(2)]
        opener = FakeOpener({
            "https://x/u": [(200, list(full)) for _ in range(20)]
        })
        with self.assertRaises(RuntimeError):
            paginate_zia(opener, "https://x/u", {}, {}, page_size=2, max_pages=5)


class PaginateZpaTest(unittest.TestCase):
    def test_uses_total_pages(self):
        opener = FakeOpener({
            "https://x/segmentGroup": [
                (200, {"list": [{"id": "1"}], "totalPages": "2"}),
                (200, {"list": [{"id": "2"}], "totalPages": "2"}),
            ]
        })
        out = paginate_zpa(opener, "https://x/segmentGroup", {}, {}, page_size=1)
        self.assertEqual([i["id"] for i in out], ["1", "2"])

    def test_single_page(self):
        opener = FakeOpener({
            "https://x/s": [(200, {"list": [{"id": "1"}], "totalPages": "1"})]
        })
        self.assertEqual(len(paginate_zpa(opener, "https://x/s", {}, {}, page_size=500)), 1)


class ComposeUrlTest(unittest.TestCase):
    def test_oneapi_zia(self):
        self.assertEqual(
            compose_url("oneapi", "zia", "urlCategories", {"customer_id": "C"}),
            "https://api.zsapi.net/zia/api/v1/urlCategories",
        )

    def test_oneapi_zpa_uses_customer(self):
        self.assertEqual(
            compose_url("oneapi", "zpa", "segmentGroup", {"customer_id": "C9"}),
            "https://api.zsapi.net/zpa/mgmtconfig/v1/admin/customers/C9/segmentGroup",
        )

    def test_oneapi_cloud_variant_gateway(self):
        self.assertEqual(
            compose_url("oneapi", "zia", "x", {"cloud": "beta"}),
            "https://api.beta.zsapi.net/zia/api/v1/x",
        )

    def test_legacy_zia(self):
        self.assertEqual(
            compose_url("legacy", "zia", "urlCategories", {"cloud": "zscalertwo"}),
            "https://zsapi.zscalertwo.net/api/v1/urlCategories",
        )

    def test_legacy_zpa(self):
        self.assertEqual(
            compose_url("legacy", "zpa", "segmentGroup", {"customer_id": "C9"}),
            "https://config.private.zscaler.com/mgmtconfig/v1/admin/customers/C9/segmentGroup",
        )

    def test_unknown_mode_raises(self):
        with self.assertRaises(ValueError):
            compose_url("nope", "zia", "x", {})


class BuildHeadersTest(unittest.TestCase):
    def test_bearer(self):
        self.assertEqual(
            build_headers("tok"), {"Authorization": "Bearer tok", "Accept": "application/json"}
        )

    def test_no_token_is_cookie_mode(self):
        # ZIA legacy authenticates by session cookie (held in the opener),
        # so there is no bearer header.
        self.assertEqual(build_headers(None), {"Accept": "application/json"})


class FetchResourceTest(unittest.TestCase):
    def test_zpa_resource_via_fake(self):
        opener = FakeOpener({
            "https://api.zsapi.net/zpa/mgmtconfig/v1/admin/customers/C/segmentGroup": [
                (200, {"list": [{"id": "1", "name": "G"}], "totalPages": "1"})
            ]
        })
        out = fetch_resource(
            "zpa_segment_group", "oneapi", {"customer_id": "C"}, "tok", opener
        )
        self.assertEqual(out, [{"id": "1", "name": "G"}])

    def test_zia_resource_passes_manifest_query(self):
        opener = FakeOpener({
            "https://zsapi.zscalertwo.net/api/v1/urlCategories": [
                (200, [{"id": "CUSTOM_1"}])
            ]
        })
        out = fetch_resource(
            "zia_url_categories", "legacy", {"cloud": "zscalertwo"}, "tok", opener
        )
        self.assertEqual(out, [{"id": "CUSTOM_1"}])
        self.assertIn("customOnly=true", opener.calls[0])


class ProductsTest(unittest.TestCase):
    def test_products_in_manifest(self):
        self.assertEqual(products_in_manifest(), ["zcc", "zia", "zpa"])


class FetchAllResilienceTest(unittest.TestCase):
    def test_one_resource_failure_does_not_block_others(self):
        import tempfile
        from tools.fetch import fetch_all, load_manifest, compose_url

        env = {
            "ZSCALER_VANITY_DOMAIN": "acme", "ZSCALER_CLOUD": "",
            "ZSCALER_CLIENT_ID": "cid", "ZSCALER_CLIENT_SECRET": "sec",
        }
        ctx = {"cloud": "", "customer_id": "C", "zcc_cloud": ""}
        pages = {
            "https://acme.zslogin.net/oauth2/v1/token": [
                (200, {"access_token": "TOK", "expires_in": "3600"})
            ] * 3,
        }
        # every registered resource answers one page per expanded path;
        # the FIRST zcc resource 404s
        broken = None
        for rt, entry in sorted(load_manifest().items()):
            for path in expand_paths(entry):
                url = compose_url("oneapi", entry["product"], path, ctx)
                if entry["product"] == "zcc" and (broken is None or broken == rt):
                    pages[url] = [(404, {"error": "not mounted"})]
                    broken = rt
                elif entry["product"] == "zpa":
                    pages[url] = [(200, {"list": [{"id": "1", "name": "x"}], "totalPages": "1"})]
                else:
                    pages[url] = [(200, [{"id": "1", "name": "x"}])]
        opener = FakeOpener(pages)
        old = sys.stderr
        sys.stderr = io.StringIO()
        try:
            with tempfile.TemporaryDirectory() as td:
                rc = fetch_all("oneapi", env, ctx, opener, td)
                written = sorted(
                    f[:-len(".json")] for f in os.listdir(td) if f.endswith(".json")
                )
            err = sys.stderr.getvalue()
        finally:
            sys.stderr = old
        self.assertEqual(rc, 1)
        self.assertNotIn(broken, written)
        # everything except the broken resource was still written
        self.assertEqual(len(written), len(load_manifest()) - 1)
        self.assertIn("FAILED", err)
        self.assertIn(broken, err)


class AcquireTokenTest(unittest.TestCase):
    def test_oneapi_posts_client_credentials(self):
        opener = FakeOpener({
            "https://acme.zslogin.net/oauth2/v1/token": [
                (200, {"access_token": "ONEAPI_TOK", "expires_in": "3600"})
            ]
        })
        env = {
            "ZSCALER_VANITY_DOMAIN": "acme", "ZSCALER_CLOUD": "",
            "ZSCALER_CLIENT_ID": "cid", "ZSCALER_CLIENT_SECRET": "sec",
        }
        token = acquire_token("oneapi", "zia", env, {}, opener)
        self.assertEqual(token, "ONEAPI_TOK")
        body_seen = opener.bodies[0].decode()
        self.assertIn("grant_type=client_credentials", body_seen)
        self.assertIn("client_id=cid", body_seen)

    def test_legacy_zpa_signin_returns_bearer(self):
        opener = FakeOpener({
            "https://config.private.zscaler.com/signin": [
                (200, {"access_token": "ZPA_TOK"})
            ]
        })
        env = {"ZPA_CLIENT_ID": "z", "ZPA_CLIENT_SECRET": "s"}
        self.assertEqual(
            acquire_token("legacy", "zpa", env, {"cloud": "zscalertwo"}, opener),
            "ZPA_TOK",
        )

    def test_legacy_zia_session_returns_none_token(self):
        # ZIA legacy yields a session cookie (held by the opener), not a
        # bearer — acquire_token returns None and the POST carries the
        # obfuscated key.
        opener = FakeOpener({
            "https://zsapi.zscalertwo.net/api/v1/authenticatedSession": [
                (200, {"authType": "ADMIN_LOGIN"})
            ]
        })
        env = {
            "ZIA_API_KEY": "abcdefghijklmnop",
            "ZIA_USERNAME": "u", "ZIA_PASSWORD": "p",
        }
        token = acquire_token("legacy", "zia", env, {"cloud": "zscalertwo"}, opener)
        self.assertIsNone(token)
        body = json.loads(opener.bodies[0].decode())
        self.assertEqual(body["username"], "u")
        self.assertIn("apiKey", body)
        self.assertIn("timestamp", body)


class AuthModeTest(unittest.TestCase):
    def test_default_is_oneapi(self):
        self.assertEqual(auth_mode_from_env({}), "oneapi")

    def test_legacy_toggle(self):
        self.assertEqual(
            auth_mode_from_env({"ZSCALER_USE_LEGACY_CLIENT": "true"}), "legacy"
        )
        self.assertEqual(
            auth_mode_from_env({"ZSCALER_USE_LEGACY_CLIENT": "1"}), "legacy"
        )

    def test_falsey_is_oneapi(self):
        self.assertEqual(
            auth_mode_from_env({"ZSCALER_USE_LEGACY_CLIENT": "false"}), "oneapi"
        )


class ZsloginHostTest(unittest.TestCase):
    def test_production_no_suffix(self):
        self.assertEqual(_zslogin_host("acme", ""), "https://acme.zslogin.net")
        self.assertEqual(_zslogin_host("acme", "PRODUCTION"), "https://acme.zslogin.net")

    def test_other_cloud_suffix(self):
        self.assertEqual(_zslogin_host("acme", "beta"), "https://acme.zsloginbeta.net")


class CaBundleTest(unittest.TestCase):
    def test_none_by_default(self):
        self.assertIsNone(ca_bundle_path({}))

    def test_requests_bundle_preferred(self):
        self.assertEqual(
            ca_bundle_path({"REQUESTS_CA_BUNDLE": "/a.pem", "SSL_CERT_FILE": "/b.pem"}),
            "/a.pem",
        )

    def test_ssl_cert_file_fallback(self):
        self.assertEqual(ca_bundle_path({"SSL_CERT_FILE": "/b.pem"}), "/b.pem")


class ConnectionHintTest(unittest.TestCase):
    def test_ssl_failures_point_at_ca_bundle(self):
        hint = connection_hint("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
        self.assertIn("REQUESTS_CA_BUNDLE", hint)

    def test_blocked_egress_points_at_proxy(self):
        self.assertIn("HTTPS_PROXY", connection_hint("Connection refused"))
        self.assertIn("HTTPS_PROXY", connection_hint("timed out"))

    def test_unknown_points_at_docs(self):
        self.assertIn("FETCH.md", connection_hint("weird failure"))


class DiagHostsTest(unittest.TestCase):
    def test_oneapi_hosts(self):
        env = {"ZSCALER_VANITY_DOMAIN": "acme", "ZSCALER_CLOUD": ""}
        self.assertEqual(
            diag_hosts(env), ["acme.zslogin.net", "api.zsapi.net"]
        )

    def test_legacy_hosts(self):
        env = {"ZSCALER_USE_LEGACY_CLIENT": "true", "ZIA_CLOUD": "zscalertwo",
               "ZCC_CLOUD": "zscalertwo"}
        self.assertEqual(
            diag_hosts(env),
            ["api-mobile.zscalertwo.net", "config.private.zscaler.com", "zsapi.zscalertwo.net"],
        )

    def test_placeholder_when_unset(self):
        self.assertEqual(diag_hosts({}), ["<vanity>.zslogin.net", "api.zsapi.net"])


class ExpandPathsTest(unittest.TestCase):
    def test_no_expand_returns_path(self):
        self.assertEqual(expand_paths({"path": "locations"}), ["locations"])

    def test_expand_substitutes_each_value(self):
        entry = {
            "path": "webApplicationRules/{rule_type}",
            "expand": {"rule_type": ["STREAMING_MEDIA", "WEBMAIL"]},
        }
        self.assertEqual(
            expand_paths(entry),
            ["webApplicationRules/STREAMING_MEDIA", "webApplicationRules/WEBMAIL"],
        )

    def test_multiple_placeholders_rejected(self):
        with self.assertRaises(ValueError):
            expand_paths({"path": "a/{x}/{y}", "expand": {"x": ["1"], "y": ["2"]}})

    def test_missing_token_rejected(self):
        with self.assertRaises(ValueError):
            expand_paths({"path": "plain", "expand": {"x": ["1"]}})

    def test_values_are_url_encoded(self):
        entry = {"path": "a/{x}", "expand": {"x": ["HAS SPACE"]}}
        self.assertEqual(expand_paths(entry), ["a/HAS%20SPACE"])


class FetchResourceExpandTest(unittest.TestCase):
    def test_concatenates_expanded_paths(self):
        # fake registry entry exercised through fetch_resource via a stub
        # manifest: easiest is to call the internal _fetch_entry_paths flow —
        # instead test through fetch_resource with a temporarily-extended
        # registry is NOT possible (registry is committed data), so test the
        # loop by composing manually:
        opener = FakeOpener({
            "https://zsapi.zscalertwo.net/api/v1/webApplicationRules/STREAMING_MEDIA": [
                (200, [{"id": "1"}])
            ],
            "https://zsapi.zscalertwo.net/api/v1/webApplicationRules/WEBMAIL": [
                (200, [{"id": "2"}])
            ],
        })
        from tools.fetch import _fetch_paths
        entry = {
            "product": "zia",
            "path": "webApplicationRules/{rule_type}",
            "pagination": "zia",
            "expand": {"rule_type": ["STREAMING_MEDIA", "WEBMAIL"]},
        }
        out = _fetch_paths(entry, "legacy", {"cloud": "zscalertwo"}, "tok", opener)
        self.assertEqual([i["id"] for i in out], ["1", "2"])


class PaginateSingleTest(unittest.TestCase):
    def test_wraps_single_object(self):
        opener = FakeOpener({
            "https://x/getWebPrivacyInfo": [(200, {"id": "1", "active": "1"})]
        })
        out = paginate_single(opener, "https://x/getWebPrivacyInfo", {}, {})
        self.assertEqual(out, [{"id": "1", "active": "1"}])

    def test_passthrough_list(self):
        opener = FakeOpener({
            "https://x/list": [(200, [{"id": "1"}, {"id": "2"}])]
        })
        out = paginate_single(opener, "https://x/list", {}, {})
        self.assertEqual(out, [{"id": "1"}, {"id": "2"}])


class PaginateZccV2Test(unittest.TestCase):
    def test_stops_on_short_page(self):
        opener = FakeOpener({
            "https://x/trusted-networks": [
                (200, {"items": [{"id": 1}], "total": 3, "offset": 0, "limit": 2, "count": 2}),
                (200, {"items": [{"id": 2}, {"id": 3}], "total": 3, "offset": 2, "limit": 2, "count": 1}),
            ]
        })
        out = paginate_zcc_v2(opener, "https://x/trusted-networks", {}, {}, per_page=2)
        self.assertEqual([i["id"] for i in out], [1, 2, 3])

    def test_empty_first_page(self):
        opener = FakeOpener({
            "https://x/t": [(200, {"items": [], "total": 0, "offset": 0, "limit": 100, "count": 0})]
        })
        self.assertEqual(paginate_zcc_v2(opener, "https://x/t", {}, {}, per_page=100), [])

    def test_total_based_termination(self):
        opener = FakeOpener({
            "https://x/t": [
                (200, {"items": [{"id": 1}, {"id": 2}], "total": 2, "offset": 0, "limit": 2, "count": 2}),
            ]
        })
        out = paginate_zcc_v2(opener, "https://x/t", {}, {}, per_page=2)
        self.assertEqual(len(out), 2)
        # only one HTTP call needed since collected == total
        self.assertEqual(len(opener.calls), 1)


class ComposeUrlZccTest(unittest.TestCase):
    def test_oneapi_zcc(self):
        self.assertEqual(
            compose_url("oneapi", "zcc",
                        "zcc/papi/public/v1/webForwardingProfile/listByCompany",
                        {"cloud": ""}),
            "https://api.zsapi.net/zcc/papi/public/v1/webForwardingProfile/listByCompany",
        )

    def test_oneapi_zcc_cloud_variant(self):
        self.assertEqual(
            compose_url("oneapi", "zcc",
                        "zcc/papi/public/v1/webTrustedNetwork/listByCompany",
                        {"cloud": "beta"}),
            "https://api.beta.zsapi.net/zcc/papi/public/v1/webTrustedNetwork/listByCompany",
        )

    def test_legacy_zcc(self):
        self.assertEqual(
            compose_url("legacy", "zcc",
                        "zcc/papi/public/v1/webForwardingProfile/listByCompany",
                        {"zcc_cloud": "zscalertwo"}),
            "https://api-mobile.zscalertwo.net/papi/zcc/papi/public/v1/webForwardingProfile/listByCompany",
        )

    def test_legacy_zcc_base_helper(self):
        self.assertEqual(
            _legacy_zcc_base("zscalertwo"),
            "https://api-mobile.zscalertwo.net/papi",
        )


class BuildHeadersZccTest(unittest.TestCase):
    def test_auth_token_header(self):
        h = build_headers("jwt123", auth_token_header=True)
        self.assertEqual(h["auth-token"], "jwt123")
        self.assertNotIn("Authorization", h)

    def test_bearer_by_default(self):
        h = build_headers("tok")
        self.assertIn("Authorization", h)
        self.assertNotIn("auth-token", h)


class AcquireTokenZccLegacyTest(unittest.TestCase):
    def test_zcc_legacy_login_returns_jwt(self):
        opener = FakeOpener({
            "https://api-mobile.zscalertwo.net/papi/auth/v1/login": [
                (200, {"jwtToken": "ZCC_JWT_TOK", "expires_in": "86400"})
            ]
        })
        env = {
            "ZCC_CLIENT_ID": "cid",
            "ZCC_CLIENT_SECRET": "sec",
            "ZCC_CLOUD": "zscalertwo",
        }
        token = acquire_token("legacy", "zcc", env, {}, opener)
        self.assertEqual(token, "ZCC_JWT_TOK")
        body = json.loads(opener.bodies[0].decode())
        self.assertEqual(body["apiKey"], "cid")
        self.assertEqual(body["secretKey"], "sec")


class DiagHostsZccTest(unittest.TestCase):
    def test_legacy_includes_zcc_host(self):
        env = {"ZSCALER_USE_LEGACY_CLIENT": "true",
               "ZIA_CLOUD": "zscalertwo", "ZCC_CLOUD": "zscalertwo"}
        hosts = diag_hosts(env)
        self.assertIn("api-mobile.zscalertwo.net", hosts)

    def test_legacy_zcc_defaults_to_zia_cloud(self):
        env = {"ZSCALER_USE_LEGACY_CLIENT": "true", "ZIA_CLOUD": "zscalertwo"}
        hosts = diag_hosts(env)
        self.assertIn("api-mobile.zscalertwo.net", hosts)


if __name__ == "__main__":
    unittest.main()
