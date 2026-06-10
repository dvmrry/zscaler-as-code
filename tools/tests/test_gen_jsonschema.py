"""Tests for tools/gen_jsonschema.py."""
import unittest

from tools.gen_jsonschema import build_schema
from tools.tfschema import load_resource


class BuildSchemaTest(unittest.TestCase):
    def test_segment_group_schema(self):
        rs = load_resource("zpa_segment_group")
        s = build_schema("zpa_segment_group", rs)
        self.assertEqual(s["$schema"], "http://json-schema.org/draft-07/schema#")
        items = s["properties"]["items"]
        per_item = items["additionalProperties"]
        self.assertEqual(per_item["required"], ["name"])
        self.assertFalse(per_item["additionalProperties"])
        self.assertEqual(per_item["properties"]["name"], {"type": "string"})
        self.assertEqual(per_item["properties"]["enabled"], {"type": "boolean"})
        apps = per_item["properties"]["applications"]
        self.assertEqual(apps["type"], "array")
        self.assertEqual(apps["items"]["properties"]["id"], {"type": "string"})

    def test_computed_only_excluded(self):
        rs = load_resource("zia_url_categories")
        s = build_schema("zia_url_categories", rs)
        props = s["properties"]["items"]["additionalProperties"]["properties"]
        self.assertNotIn("category_id", props)
        self.assertNotIn("val", props)
        self.assertIn("configured_name", props)

    def test_set_block_carries_unique_items(self):
        rs = load_resource("zia_url_categories")
        s = build_schema("zia_url_categories", rs)
        props = s["properties"]["items"]["additionalProperties"]["properties"]
        self.assertTrue(props["scopes"].get("uniqueItems"))
        self.assertNotIn("uniqueItems", props["url_keyword_counts"])


if __name__ == "__main__":
    unittest.main()
