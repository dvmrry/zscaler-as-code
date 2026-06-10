"""Tests for tools/fetch.py. All canned responses are fictional."""
import io
import json
import unittest

from tools.fetch import load_manifest, manifest_entry


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


if __name__ == "__main__":
    unittest.main()
