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


class EditorSettingsTest(unittest.TestCase):
    def test_every_generated_type_mapped(self):
        from tools.gen_jsonschema import build_editor_settings
        from tools.registry import generated_types

        settings = build_editor_settings()
        mappings = settings["json.schemas"]
        self.assertEqual(len(mappings), len(generated_types()))
        for m in mappings:
            self.assertEqual(len(m["fileMatch"]), 1)
            self.assertTrue(m["fileMatch"][0].startswith("config/*/"))
            self.assertTrue(m["url"].startswith("./schemas/tfvars/"))



class SingleSetBlockTest(unittest.TestCase):
    def test_set_block_with_max_items_one_is_object_not_array(self):
        # block_is_single must take priority over set handling: the ZIA
        # ID-group pattern (40+ blocks) is ONE object with list members
        from tools.gen_jsonschema import build_schema
        from tools.tfschema import load_resource
        schema = build_schema("zia_ssl_inspection_rules",
                              load_resource("zia_ssl_inspection_rules"))
        item_props = schema["properties"]["items"][
            "additionalProperties"]["properties"]
        departments = item_props["departments"]
        self.assertEqual(departments.get("type"), "object")
        self.assertNotIn("uniqueItems", departments)


if __name__ == "__main__":
    unittest.main()
