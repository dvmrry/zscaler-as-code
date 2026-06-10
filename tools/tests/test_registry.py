"""Tests for tools/registry.py."""
import unittest

from tools.registry import fetch_entry, generated_types, load_registry


class RegistryTest(unittest.TestCase):
    def test_generated_types_sorted(self):
        self.assertEqual(
            generated_types(),
            [
                "zia_cloud_app_control_rule",
                "zia_location_management",
                "zia_ssl_inspection_rules",
                "zia_url_categories",
                "zpa_application_segment",
                "zpa_segment_group",
                "zpa_server_group",
            ],
        )

    def test_fetch_entry_shape(self):
        e = fetch_entry("zpa_segment_group")
        self.assertEqual(e["product"], "zpa")
        self.assertEqual(e["path"], "segmentGroup")
        self.assertEqual(e["pagination"], "zpa")

    def test_fetch_entry_unknown_raises(self):
        with self.assertRaises(KeyError):
            fetch_entry("zpa_nope")

    def test_every_entry_has_product(self):
        for rt, e in load_registry().items():
            self.assertIn(e["product"], ("zia", "zpa"), rt)

    def test_generators_and_fetch_consume_registry(self):
        import tools.fetch as fetch
        for rt in generated_types():
            self.assertIn(rt, load_registry())
        self.assertEqual(sorted(fetch.products_in_manifest()), ["zia", "zpa"])
