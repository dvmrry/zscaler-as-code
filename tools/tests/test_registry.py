"""Tests for tools/registry.py."""
import unittest

from tools.headroom_report import provider_resources
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
            sorted(provider_resources()),
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
