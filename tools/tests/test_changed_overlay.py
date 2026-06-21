import unittest

from tools import changed


class ChangedOverlayTest(unittest.TestCase):
    def test_overlay_config_path_maps_to_pair(self):
        # A diff path under the overlay must map to (tenant, resource).
        pairs = changed.pairs_from_paths(
            ["_local/config/acme/zia_url_filtering_rules.auto.tfvars.json"],
            overlay="_local",
        )
        self.assertIn(("acme", "zia_url_filtering_rules"), pairs)

    def test_overlay_lookup_path_maps_to_pair(self):
        pairs = changed.pairs_from_paths(
            ["_local/lookups/acme/zia_url_categories.lookup.json"],
            overlay="_local",
        )
        self.assertIn(("acme", "zia_url_categories"), pairs)

    def test_root_config_path_still_maps(self):
        pairs = changed.pairs_from_paths(
            ["config/demo/zia_url_filtering_rules.auto.tfvars.json"],
            overlay="_local",
        )
        self.assertIn(("demo", "zia_url_filtering_rules"), pairs)

    def test_deployment_json_is_global_trigger(self):
        self.assertIn("deployment.json", changed.GLOBAL_PREFIXES)


if __name__ == "__main__":
    unittest.main()
