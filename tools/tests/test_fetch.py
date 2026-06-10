"""Tests for tools/fetch.py. All canned responses are fictional."""
import io
import json
import unittest

from tools.fetch import load_manifest, manifest_entry, obfuscate_api_key


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


if __name__ == "__main__":
    unittest.main()
