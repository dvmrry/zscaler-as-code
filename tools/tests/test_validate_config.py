"""Tests for tools/validate_config.py (pure logic; jsonschema optional)."""
import unittest

from tools.validate_config import config_pairs


class ConfigPairsTest(unittest.TestCase):
    def test_pairs_existing_config_to_schema(self):
        pairs = config_pairs()
        # zs2 sample config exists for the four original resources at least
        rts = sorted(rt for (_t, rt, _c, _s) in pairs)
        self.assertIn("zpa_segment_group", rts)
        for tenant, rt, cfg, schema in pairs:
            self.assertTrue(cfg.endswith(rt + ".auto.tfvars.json"))
            self.assertTrue(schema.endswith(rt + ".schema.json"))

    def test_unknown_config_raises(self):
        import os, tempfile
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, "zsX"))
            bad = os.path.join(td, "zsX", "zia_nope.auto.tfvars.json")
            with open(bad, "w", encoding="utf-8") as f:
                f.write("{}")
            with self.assertRaises(SystemExit):
                config_pairs(config_root=td)
