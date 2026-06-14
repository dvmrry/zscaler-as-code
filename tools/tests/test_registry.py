"""Tests for tools/registry.py."""
import unittest

from tools.registry import (
    derive_entry,
    derived_types,
    fetch_entry,
    generated_types,
    load_registry,
    reload_registry,
)


class RegistryTest(unittest.TestCase):
    def test_generated_types_sorted(self):
        self.assertEqual(
            generated_types(),
            [
                "zcc_failopen_policy",
                "zcc_forwarding_profile",
                "zcc_trusted_network",
                "zcc_web_privacy",
                "zia_cloud_app_control_rule",
                "zia_location_management",
                "zia_rule_labels",
                "zia_ssl_inspection_rules",
                "zia_url_categories",
                "zia_url_filtering_rules",
                "zpa_app_connector_group",
                "zpa_application_segment",
                "zpa_application_server",
                "zpa_policy_access_rule",
                "zpa_policy_access_rule_reorder",
                "zpa_segment_group",
                "zpa_server_group",
            ],
        )

    def test_derived_resource_has_no_fetch(self):
        # a derived resource is generated from another's pull, never fetched
        self.assertEqual(derived_types(), ["zpa_policy_access_rule_reorder"])
        d = derive_entry("zpa_policy_access_rule_reorder")
        self.assertEqual(d["from"], "zpa_policy_access_rule")
        with self.assertRaises(KeyError):
            fetch_entry("zpa_policy_access_rule_reorder")
        self.assertIsNone(derive_entry("zpa_policy_access_rule"))

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
            self.assertIn(e["product"], ("zcc", "zia", "zpa"), rt)

    def test_generators_and_fetch_consume_registry(self):
        import tools.fetch as fetch
        for rt in generated_types():
            self.assertIn(rt, load_registry())
        self.assertEqual(sorted(fetch.products_in_manifest()), ["zcc", "zia", "zpa"])

    def test_reload_registry(self):
        reg = reload_registry()
        self.assertEqual(reg, load_registry())
        self.assertIn("zpa_segment_group", reg)
