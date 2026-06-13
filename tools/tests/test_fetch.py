"""Tests for tools/fetch.py. All canned responses are fictional."""
import io
import json
import os
import sys
import unittest

from tools.fetch import load_manifest, manifest_entry, obfuscate_api_key, paginate_zia, paginate_zpa, paginate_single, paginate_zcc_v2, build_headers, compose_url, fetch_resource, acquire_token, products_in_manifest, auth_mode_from_env, _zslogin_host, _legacy_zcc_base, ca_bundle_path, connection_hint, diag_hosts, expand_paths, _retry_delay, _request_with_retry, _RETRY_BASE, _RETRY_CAP


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
        # Fictional key/timestamp; output is a hardcoded oracle (not derived
        # from a mirror of the implementation): high="000000" picks key[0]
        # six times ("a"), low="000000" picks key[0+2] six times ("c").
        self.assertEqual(
            obfuscate_api_key("abcdefghijklmnop", "1700000000"), "aaaaaacccccc"
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

    def test_envelope_unwraps_wrapped_pages(self):
        # ZCC v1 trusted networks wrap the page:
        # {"totalCount": N, "trustedNetworkContracts": [...]}
        opener = FakeOpener({
            "https://x/webTrustedNetwork/listByCompany": [
                (200, {"totalCount": 2, "trustedNetworkContracts": [
                    {"id": "1"}, {"id": "2"}
                ]})
            ]
        })
        out = paginate_zia(
            opener, "https://x/webTrustedNetwork/listByCompany", {}, {},
            page_size=500, envelope="trustedNetworkContracts",
        )
        self.assertEqual([i["id"] for i in out], ["1", "2"])

    def test_envelope_missing_key_is_empty(self):
        opener = FakeOpener({"https://x/u": [(200, {"totalCount": 0})]})
        self.assertEqual(
            paginate_zia(opener, "https://x/u", {}, {}, page_size=500,
                         envelope="trustedNetworkContracts"),
            [],
        )

    def test_unwrapped_dict_without_envelope_still_raises(self):
        opener = FakeOpener({"https://x/u": [(200, {"not": "a list"})]})
        with self.assertRaises(RuntimeError):
            paginate_zia(opener, "https://x/u", {}, {}, page_size=500)

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


class FetchAllAuthIsolationTest(unittest.TestCase):
    def test_one_product_auth_failure_does_not_block_others(self):
        # zcc token acquisition fails (HTTP 500) while zia/zpa succeed: zcc
        # resources are marked auth-failed but every zia/zpa resource is
        # still fetched and written. fetch_all's safety contract.
        import tempfile
        from tools.fetch import fetch_all, load_manifest

        env = {
            "ZSCALER_VANITY_DOMAIN": "acme", "ZSCALER_CLOUD": "",
            "ZSCALER_CLIENT_ID": "cid", "ZSCALER_CLIENT_SECRET": "sec",
        }
        ctx = {"cloud": "", "customer_id": "C", "zcc_cloud": ""}
        # products acquire in order zcc, zia, zpa against one token URL;
        # fail the first (zcc) call only.
        pages = {
            "https://acme.zslogin.net/oauth2/v1/token": [
                (500, {"error": "server"}),
                (200, {"access_token": "TOK", "expires_in": "3600"}),
                (200, {"access_token": "TOK", "expires_in": "3600"}),
            ],
        }
        zcc_resources = set()
        for rt, entry in sorted(load_manifest().items()):
            for path in expand_paths(entry):
                url = compose_url("oneapi", entry["product"], path, ctx)
                if entry["product"] == "zcc":
                    zcc_resources.add(rt)
                elif entry["product"] == "zpa":
                    pages[url] = [(200, {"list": [{"id": "1"}], "totalPages": "1"})]
                else:
                    pages[url] = [(200, [{"id": "1"}])]
        opener = FakeOpener(pages)
        old = sys.stderr
        sys.stderr = io.StringIO()
        try:
            with tempfile.TemporaryDirectory() as td:
                rc = fetch_all("oneapi", env, ctx, opener, td)
                written = set(
                    f[:-len(".json")] for f in os.listdir(td) if f.endswith(".json")
                )
            err = sys.stderr.getvalue()
        finally:
            sys.stderr = old
        self.assertEqual(rc, 1)
        # no zcc resource was written; all of them are reported auth-failed
        self.assertEqual(written & zcc_resources, set())
        for rt in zcc_resources:
            self.assertIn(rt, err)
        self.assertIn("auth failed", err)
        # every non-zcc resource was written successfully
        non_zcc = set(load_manifest()) - zcc_resources
        self.assertEqual(written, non_zcc)


class ExpandSelectorsTest(unittest.TestCase):
    def test_product_token_expands_to_its_resources(self):
        from tools.fetch import expand_selectors, load_manifest, manifest_entry

        out = expand_selectors(["zcc"])
        self.assertTrue(out)
        for rt in out:
            self.assertEqual(manifest_entry(rt)["product"], "zcc")
        self.assertEqual(
            out,
            set(rt for rt in load_manifest()
                if manifest_entry(rt)["product"] == "zcc"),
        )

    def test_products_and_resources_mix(self):
        from tools.fetch import expand_selectors

        out = expand_selectors(["zia", "zpa_segment_group"])
        self.assertIn("zia_rule_labels", out)
        self.assertIn("zpa_segment_group", out)
        self.assertNotIn("zcc_web_privacy", out)

    def test_disable_one_product_pattern(self):
        # The pipeline pattern for "ZCC off until OneAPI": RESOURCE="zia zpa"
        from tools.fetch import expand_selectors, load_manifest, manifest_entry

        out = expand_selectors(["zia", "zpa"])
        zcc = set(rt for rt in load_manifest()
                  if manifest_entry(rt)["product"] == "zcc")
        self.assertEqual(out & zcc, set())
        self.assertEqual(len(out), len(load_manifest()) - len(zcc))

    def test_unknown_passes_through_for_loud_validation(self):
        from tools.fetch import expand_selectors

        self.assertIn("nonsense", expand_selectors(["nonsense"]))

    def test_empty_means_everything(self):
        from tools.fetch import expand_selectors

        self.assertIsNone(expand_selectors([]))


class MainZpaCustomerIdTest(unittest.TestCase):
    """ZPA_CUSTOMER_ID is required only when ZPA is in scope, so scoped
    ZIA/ZCC fetches do not demand ZPA credentials they never use."""

    def _run_main(self, argv, env):
        # Patch real_opener (no network) and fetch_all (we assert on ctx only)
        # so main() runs its env/ctx-building logic hermetically.
        import tools.fetch as F
        captured = {}

        def fake_fetch_all(auth_mode, env, ctx, opener, out_dir, only=None):
            captured["ctx"] = ctx
            captured["only"] = only
            return 0

        old_opener = F.real_opener
        old_fetch_all = F.fetch_all
        old_environ = os.environ
        F.real_opener = lambda: (lambda *a, **k: (200, b"{}"))
        F.fetch_all = fake_fetch_all
        os.environ = env
        try:
            rc = F.main(argv)
        finally:
            F.real_opener = old_opener
            F.fetch_all = old_fetch_all
            os.environ = old_environ
        return rc, captured

    def test_zia_only_scope_does_not_require_zpa_customer_id(self):
        env = {"ZIA_CLOUD": "zscalertwo"}  # no ZPA_CUSTOMER_ID
        rc, captured = self._run_main(["t", "zia_url_categories"], env)
        self.assertEqual(rc, 0)
        self.assertEqual(captured["ctx"]["customer_id"], "")

    def test_zpa_in_scope_requires_zpa_customer_id(self):
        env = {"ZIA_CLOUD": "zscalertwo"}  # ZPA scoped but no customer id
        with self.assertRaises(SystemExit) as cm:
            self._run_main(["t", "zpa_app_connector_group"], env)
        self.assertIn("ZPA_CUSTOMER_ID", str(cm.exception))

    def test_unscoped_still_requires_zpa_customer_id(self):
        env = {"ZIA_CLOUD": "zscalertwo"}  # full fetch includes zpa
        with self.assertRaises(SystemExit) as cm:
            self._run_main(["t"], env)
        self.assertIn("ZPA_CUSTOMER_ID", str(cm.exception))

    def test_zpa_scope_passes_customer_id_through(self):
        env = {"ZIA_CLOUD": "zscalertwo", "ZPA_CUSTOMER_ID": "C42"}
        rc, captured = self._run_main(["t", "zpa_app_connector_group"], env)
        self.assertEqual(rc, 0)
        self.assertEqual(captured["ctx"]["customer_id"], "C42")


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


class FailureHintsTest(unittest.TestCase):
    def test_auth_failure_emits_credential_hint_not_404(self):
        from tools.fetch import failure_hints
        hints = " ".join(failure_hints(
            ["auth failed: missing required env var ZIA_API_KEY"]
        ))
        self.assertIn("auth FAILED", hints)
        self.assertNotIn("not mounted", hints)

    def test_401_emits_token_scope_hint_not_404(self):
        from tools.fetch import failure_hints
        hints = " ".join(failure_hints(
            ["GET https://api.zsapi.net/zia/api/v1/urlCategories returned HTTP 401"]
        ))
        self.assertIn("401/403", hints)
        self.assertNotIn("not mounted", hints)

    def test_404_emits_path_or_entitlement_hint(self):
        from tools.fetch import failure_hints
        hints = " ".join(failure_hints(["GET https://x/y returned HTTP 404"]))
        self.assertIn("not mounted", hints)

    def test_404_scoped_appends_unscoped_note(self):
        from tools.fetch import failure_hints
        hints = " ".join(failure_hints(
            ["GET https://x/y returned HTTP 404"], scoped=True
        ))
        self.assertIn("unscoped fetch", hints)

    def test_404_unscoped_has_no_unscoped_note(self):
        from tools.fetch import failure_hints
        hints = " ".join(failure_hints(["GET https://x/y returned HTTP 404"]))
        self.assertNotIn("unscoped fetch", hints)

    def test_5xx_emits_transient_hint(self):
        from tools.fetch import failure_hints
        hints = " ".join(failure_hints(["GET https://x/y returned HTTP 503"]))
        self.assertIn("transient", hints)

    def test_mixed_failures_emit_all_relevant_hints(self):
        from tools.fetch import failure_hints
        hints = " ".join(failure_hints([
            "auth failed: missing required env var ZIA_API_KEY",
            "GET https://x/y returned HTTP 404",
        ]))
        self.assertIn("auth FAILED", hints)
        self.assertIn("not mounted", hints)

    def test_always_reassures_about_successful_pulls(self):
        from tools.fetch import failure_hints
        hints = failure_hints(["GET https://x/y returned HTTP 404"])
        self.assertTrue(any("Successful pulls above" in h for h in hints))


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
        # The registry's gateway-only "zcc/papi/" prefix is stripped for the
        # legacy mobile-portal base (which already ends in /papi), so /papi/
        # appears exactly once.
        self.assertEqual(
            compose_url("legacy", "zcc",
                        "zcc/papi/public/v1/webForwardingProfile/listByCompany",
                        {"zcc_cloud": "zscalertwo"}),
            "https://api-mobile.zscalertwo.net/papi/public/v1/webForwardingProfile/listByCompany",
        )

    def test_legacy_zcc_path_without_prefix_unchanged(self):
        # A path that does not carry the gateway prefix is appended as-is.
        self.assertEqual(
            compose_url("legacy", "zcc", "papi/public/v1/x",
                        {"zcc_cloud": "zscalertwo"}),
            "https://api-mobile.zscalertwo.net/papi/papi/public/v1/x",
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



class MalformedAuthResponseTest(unittest.TestCase):
    """HTTP 200 with an unexpected body must become an actionable
    per-product SystemExit — a bare KeyError here once escaped
    fetch_all's per-product isolation and aborted every remaining
    fetch."""

    ENV = {
        "ZSCALER_VANITY_DOMAIN": "acme", "ZSCALER_CLOUD": "",
        "ZSCALER_CLIENT_ID": "cid", "ZSCALER_CLIENT_SECRET": "sec",
    }

    def test_200_missing_token_key_is_actionable_systemexit(self):
        opener = FakeOpener({
            "https://acme.zslogin.net/oauth2/v1/token": [
                (200, {"token_type": "bearer"})
            ]
        })
        with self.assertRaises(SystemExit) as ctx:
            acquire_token("oneapi", "zia", self.ENV, {}, opener)
        self.assertIn("access_token", str(ctx.exception))

    def test_200_non_json_body_is_actionable_systemexit(self):
        from tools.fetch import _token_field
        with self.assertRaises(SystemExit) as ctx:
            _token_field(b"<html>maintenance</html>", "access_token",
                         "OneAPI token")
        self.assertIn("not JSON", str(ctx.exception))

    def test_200_json_string_body_is_actionable_systemexit(self):
        # FakeOpener json-encodes payloads, so a bare string arrives as
        # VALID JSON that is not a dict — the guard must catch that too
        opener = FakeOpener({
            "https://acme.zslogin.net/oauth2/v1/token": [
                (200, "maintenance")
            ]
        })
        with self.assertRaises(SystemExit) as ctx:
            acquire_token("oneapi", "zia", self.ENV, {}, opener)
        self.assertIn("access_token", str(ctx.exception))

    def test_message_never_echoes_the_body(self):
        opener = FakeOpener({
            "https://acme.zslogin.net/oauth2/v1/token": [
                (200, {"error": "secret-ish diagnostic"})
            ]
        })
        with self.assertRaises(SystemExit) as ctx:
            acquire_token("oneapi", "zia", self.ENV, {}, opener)
        self.assertNotIn("secret-ish", str(ctx.exception))


class RetryDelayTest(unittest.TestCase):
    def test_numeric_retry_after_wins_and_is_capped(self):
        # Retry-After (delta-seconds) is honored over exponential backoff
        self.assertEqual(_retry_delay(0, "2"), 2.0)
        self.assertEqual(_retry_delay(4, "3"), 3.0)  # beats 2**4 exponential
        # a hostile/huge value is capped
        self.assertEqual(_retry_delay(0, str(_RETRY_CAP + 100)), _RETRY_CAP)
        self.assertEqual(_retry_delay(0, "-5"), 0.0)  # never negative

    def test_exponential_when_absent_or_unparseable(self):
        self.assertEqual(_retry_delay(0, None), _RETRY_BASE)
        self.assertEqual(_retry_delay(2, None), _RETRY_BASE * 4)
        # HTTP-date Retry-After is not parsed -> exponential fallback
        self.assertEqual(
            _retry_delay(0, "Wed, 21 Oct 2099 07:28:00 GMT"), _RETRY_BASE)
        self.assertEqual(_retry_delay(99, None), _RETRY_CAP)  # capped


class RequestWithRetryTest(unittest.TestCase):
    def test_retries_429_then_succeeds(self):
        # the field case: rate-limited GET retried, then 200
        responses = iter([(429, b"", "1"), (429, b"", None), (200, b'{"ok":1}', None)])
        slept = []
        status, body = _request_with_retry(lambda: next(responses), slept.append)
        self.assertEqual(status, 200)
        self.assertEqual(body, b'{"ok":1}')
        self.assertEqual(len(slept), 2)         # slept once before each retry
        self.assertEqual(slept[0], 1.0)         # honored Retry-After "1"

    def test_gives_up_after_max_retries(self):
        slept = []
        status, body = _request_with_retry(
            lambda: (429, b"limited", None), slept.append, max_retries=3)
        self.assertEqual(status, 429)           # returns the last 429, not an exception
        self.assertEqual(body, b"limited")
        self.assertEqual(len(slept), 3)         # exactly max_retries waits

    def test_non_429_returns_immediately_without_sleeping(self):
        slept = []
        status, body = _request_with_retry(lambda: (200, b"ok", None), slept.append)
        self.assertEqual((status, body), (200, b"ok"))
        self.assertEqual(slept, [])
        # a non-rate-limit error is passed straight through, never retried
        status, _ = _request_with_retry(lambda: (404, b"", None), slept.append)
        self.assertEqual(status, 404)
        self.assertEqual(slept, [])


if __name__ == "__main__":
    unittest.main()
