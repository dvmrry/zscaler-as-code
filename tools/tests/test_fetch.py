"""Tests for tools/fetch.py. All canned responses are fictional."""
import io
import json
import unittest

from tools.fetch import load_manifest, manifest_entry, obfuscate_api_key, paginate_zia, paginate_zpa, build_headers, compose_url


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
            self.assertIn(e["product"], ("zia", "zpa"), rt)
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
            "https://api.zscaler.com/zia/api/v1/urlCategories",
        )

    def test_oneapi_zpa_uses_customer(self):
        self.assertEqual(
            compose_url("oneapi", "zpa", "segmentGroup", {"customer_id": "C9"}),
            "https://api.zscaler.com/zpa/mgmtconfig/v1/admin/customers/C9/segmentGroup",
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


if __name__ == "__main__":
    unittest.main()
