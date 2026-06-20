import os
import unittest

from tools import gen_env


class GenEnvOverlayTest(unittest.TestCase):
    def test_demo_module_source_byte_identical(self):
        # demo stays at root: relpath must equal the historical hardcoded string.
        text = gen_env.render_env_main("zia_url_filtering_rules", "demo",
                                       os.path.join("envs", "demo", "zia_url_filtering_rules"))
        self.assertIn('source = "../../../modules/zia_url_filtering_rules"', text)

    def test_overlay_module_source_is_relpath_with_extra_level(self):
        env_dir = os.path.join("_local", "envs", "acme", "zia_url_filtering_rules")
        text = gen_env.render_env_main("zia_url_filtering_rules", "acme", env_dir)
        self.assertIn('source = "../../../../modules/zia_url_filtering_rules"', text)

    def test_source_never_bare_registry_form(self):
        # A source not starting with ./ ../ or / is read as a Terraform REGISTRY
        # ref. Guard: our generated source is always a path.
        env_dir = os.path.join("_local", "envs", "acme", "zia_x")
        text = gen_env.render_env_main("zia_x", "acme", env_dir)
        line = [l for l in text.splitlines() if l.strip().startswith("source = \"")][-1]
        val = line.split('"')[1]
        self.assertTrue(val.startswith(("./", "../", "/")), "registry-form source: %r" % val)

    def test_readme_config_path_is_overlay_agnostic(self):
        # The README is regenerated into $(OVERLAY)/envs/<t>/<rt>/ for real
        # tenants but is byte-identical for demo. It must phrase the config path
        # RELATIVE TO THE ENV ROOT (../../../config/...), which is identical at
        # both depths — never the root-anchored "config/<tenant>/..." form that
        # is wrong for an overlay tenant.
        text = gen_env.render_env_readme("zia_url_filtering_rules", "acme")
        self.assertNotIn("`config/acme/", text)
        self.assertIn("../../../config/acme/zia_url_filtering_rules.auto.tfvars.json", text)


if __name__ == "__main__":
    unittest.main()
