"""Tests for tools/extract_schemas.py. Fixture data is fictional."""
import unittest

from tools.extract_schemas import split_schemas


FAKE_COMBINED = {
    "format_version": "1.0",
    "provider_schemas": {
        "registry.terraform.io/zscaler/zia": {
            "resource_schemas": {"zia_url_categories": {"version": 0}}
        },
        "registry.terraform.io/zscaler/zpa": {
            "resource_schemas": {"zpa_application_segment": {"version": 0}}
        },
        "registry.terraform.io/zscaler/zcc": {
            "resource_schemas": {"zcc_forwarding_profile": {"version": 0}}
        },
    },
}


class SplitSchemasTest(unittest.TestCase):
    def test_splits_into_zia_zpa_and_zcc(self):
        result = split_schemas(FAKE_COMBINED)
        self.assertEqual(sorted(result), ["zcc", "zia", "zpa"])

    def test_preserves_provider_subtree(self):
        result = split_schemas(FAKE_COMBINED)
        self.assertIn("zia_url_categories", result["zia"]["resource_schemas"])
        self.assertIn(
            "zpa_application_segment", result["zpa"]["resource_schemas"]
        )
        self.assertIn(
            "zcc_forwarding_profile", result["zcc"]["resource_schemas"]
        )

    def test_missing_provider_raises(self):
        with self.assertRaises(KeyError):
            split_schemas({"provider_schemas": {}})

    def test_partially_missing_provider_raises(self):
        only_zia = {
            "provider_schemas": {
                "registry.terraform.io/zscaler/zia": {"resource_schemas": {}}
            }
        }
        with self.assertRaises(KeyError):
            split_schemas(only_zia)


if __name__ == "__main__":
    unittest.main()
