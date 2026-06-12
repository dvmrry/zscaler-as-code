"""Tests for tools/gen_env.py."""
import os
import tempfile
import unittest

from tools.gen_env import render_env_main, render_env_test


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

    def test_no_backend_block_by_default(self):
        out = render_env_main("zpa_segment_group", "zs2")
        self.assertNotIn('backend "', out)

    def test_backend_block_is_partial_with_derived_key_hint(self):
        # backend=azurerm emits an EMPTY (partial) backend block — values
        # come from -backend-config at init; the per-root state key is
        # documented inline so the operator can see the blob layout.
        out = render_env_main("zpa_segment_group", "zs2", backend="azurerm")
        self.assertIn('backend "azurerm" {', out)
        self.assertIn("zs2/zpa_segment_group.tfstate", out)
        # partial: no storage values baked into the public template
        self.assertNotIn("storage_account_name", out)


class GenerateEnvTest(unittest.TestCase):
    def test_writes_root_files(self):
        from tools.gen_env import generate_env
        with tempfile.TemporaryDirectory() as td:
            generate_env("zs2", out_root=td, fmt=False)
            base = os.path.join(td, "zs2", "zpa_segment_group")
            self.assertTrue(os.path.exists(os.path.join(base, "main.tf")))
            self.assertTrue(os.path.exists(os.path.join(base, "README.md")))


class RenderEnvTestTest(unittest.TestCase):
    def test_mock_provider_matches_product(self):
        out = render_env_test("zpa_segment_group", "zs2", has_config=False)
        self.assertIn('mock_provider "zpa" {}', out)
        self.assertIn("command = plan", out)
        self.assertIn("items = {}", out)

    def test_zia_mock(self):
        out = render_env_test("zia_url_categories", "zs2", has_config=False)
        self.assertIn('mock_provider "zia" {}', out)

    def test_no_config_plan_when_has_config_false(self):
        out = render_env_test("zpa_segment_group", "zs2", has_config=False)
        self.assertNotIn("config_plan", out)

    def test_config_plan_present_when_has_config_true(self):
        out = render_env_test("zpa_segment_group", "zs2", has_config=True)
        self.assertIn('run "config_plan"', out)
        self.assertIn("command = plan", out)

    def test_config_plan_file_path_correct(self):
        # path uses tenant + resource_type
        out = render_env_test("zpa_segment_group", "zs2", has_config=True)
        self.assertIn(
            'file("../../../config/zs2/zpa_segment_group.auto.tfvars.json")',
            out,
        )

    def test_config_plan_file_path_demo_tenant(self):
        out = render_env_test("zia_url_categories", "demo", has_config=True)
        self.assertIn(
            'file("../../../config/demo/zia_url_categories.auto.tfvars.json")',
            out,
        )

    def test_empty_plan_still_present_when_has_config_true(self):
        out = render_env_test("zpa_segment_group", "zs2", has_config=True)
        self.assertIn('run "empty_plan"', out)
        self.assertIn("items = {}", out)


class BackendMarkerTest(unittest.TestCase):
    def test_backend_choice_survives_regeneration(self):
        # The backend choice is data (envs/<t>/.backend): a later regen
        # WITHOUT the backend argument — exactly what check-envs does —
        # must reproduce the same roots, not revert them to local state.
        from tools.gen_env import generate_env
        with tempfile.TemporaryDirectory() as td:
            generate_env("zs2", out_root=td, fmt=False, backend="azurerm")
            main_path = os.path.join(td, "zs2", "zpa_segment_group", "main.tf")
            with open(main_path, encoding="utf-8") as f:
                first = f.read()
            self.assertIn('backend "azurerm"', first)
            generate_env("zs2", out_root=td, fmt=False)  # no backend arg
            with open(main_path, encoding="utf-8") as f:
                second = f.read()
            self.assertEqual(first, second)

    def test_no_marker_no_backend(self):
        from tools.gen_env import generate_env
        with tempfile.TemporaryDirectory() as td:
            generate_env("zs2", out_root=td, fmt=False)
            self.assertFalse(
                os.path.exists(os.path.join(td, "zs2", ".backend"))
            )
            with open(os.path.join(td, "zs2", "zpa_segment_group", "main.tf"), encoding="utf-8") as f:
                self.assertNotIn('backend "', f.read())


class GenerateEnvWritesTest(unittest.TestCase):
    def test_writes_smoke_test(self):
        from tools.gen_env import generate_env
        with tempfile.TemporaryDirectory() as td:
            generate_env("zs2", out_root=td, fmt=False)
            self.assertTrue(os.path.exists(os.path.join(
                td, "zs2", "zpa_segment_group", "tests", "smoke.tftest.hcl"
            )))



class ReadmeContentTest(unittest.TestCase):
    def test_readme_names_tenant_resource_and_regen_command(self):
        from tools.gen_env import render_env_readme
        text = render_env_readme("acme", "zpa_segment_group")
        self.assertIn("acme", text)
        self.assertIn("zpa_segment_group", text)
        self.assertIn("make gen-env", text)


if __name__ == "__main__":
    unittest.main()
