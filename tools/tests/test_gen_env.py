"""Tests for tools/gen_env.py."""
import os
import tempfile
import unittest

from tools.gen_env import render_env_main


class RenderEnvMainTest(unittest.TestCase):
    def test_zpa_segment_group_root(self):
        out = render_env_main("zpa_segment_group", "zs2")
        self.assertIn("# GENERATED", out)
        self.assertIn('source = "../../../modules/zpa_segment_group"', out)
        self.assertIn("items = var.items", out)
        self.assertIn('variable "items"', out)
        self.assertIn("type = any", out)
        self.assertIn("zscaler/zpa", out)

    def test_zia_provider(self):
        out = render_env_main("zia_url_categories", "zs2")
        self.assertIn("zscaler/zia", out)
        self.assertNotIn("zscaler/zpa", out)

    def test_label_is_opaque_string_only(self):
        # any label works; never parsed
        for label in ("zs2", "zscalertwo", "dev", "gov-beta_1"):
            out = render_env_main("zpa_segment_group", label)
            self.assertIn(label, out)


class GenerateEnvTest(unittest.TestCase):
    def test_writes_root_files(self):
        from tools.gen_env import generate_env
        with tempfile.TemporaryDirectory() as td:
            generate_env("zs2", out_root=td, fmt=False)
            base = os.path.join(td, "zs2", "zpa_segment_group")
            self.assertTrue(os.path.exists(os.path.join(base, "main.tf")))
            self.assertTrue(os.path.exists(os.path.join(base, "README.md")))


if __name__ == "__main__":
    unittest.main()
