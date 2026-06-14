"""Tests for tools/tfschema.py against the committed provider dumps."""
import unittest

from tools.tfschema import (
    classify_attributes,
    load_resource,
    resource_input_attrs,
)
from tools.tfschema import hcl_type, json_schema_type


class LoadResourceTest(unittest.TestCase):
    def test_loads_known_resource(self):
        rs = load_resource("zpa_segment_group")
        self.assertIn("name", rs["block"]["attributes"])

    def test_unknown_resource_raises(self):
        with self.assertRaises(KeyError):
            load_resource("zpa_no_such_resource")

    def test_resource_must_be_prefixed(self):
        with self.assertRaises(KeyError):
            load_resource("segment_group")


class ClassifyTest(unittest.TestCase):
    def test_segment_group_classification(self):
        rs = load_resource("zpa_segment_group")
        cls = classify_attributes(rs["block"])
        self.assertEqual(cls["required"], ["name"])
        self.assertEqual(
            cls["optional"], ["description", "enabled", "microtenant_id"]
        )
        self.assertEqual(cls["computed_only"], ["id"])

    def test_url_categories_excludes_computed_only(self):
        rs = load_resource("zia_url_categories")
        cls = classify_attributes(rs["block"])
        for attr in ("category_id", "id", "val"):
            self.assertIn(attr, cls["computed_only"])
            self.assertNotIn(attr, cls["optional"])
        self.assertIn("configured_name", cls["optional"])


class ResourceInputAttrsTest(unittest.TestCase):
    def test_drops_top_level_optional_computed_id(self):
        # reorder's top-level id is optional+computed: classify keeps it as an
        # input, resource_input_attrs drops it (provider rejects setting it).
        block = load_resource("zpa_policy_access_rule_reorder")["block"]
        self.assertIn("id", classify_attributes(block)["optional"])
        ria = resource_input_attrs(block)
        self.assertNotIn("id", ria["optional"])
        self.assertIn("id", ria["computed_only"])

    def test_keeps_nested_block_reference_id(self):
        # a computed id inside a NESTED block (vpn_credentials reference) is a
        # real input — resource_input_attrs only touches the top-level block.
        block = load_resource("zia_location_management")["block"]
        vc = block["block_types"]["vpn_credentials"]["block"]
        cls = classify_attributes(vc)
        self.assertIn("id", cls["optional"] + cls["required"])

    def test_no_op_when_id_is_computed_only(self):
        # normal resources have computed-only id (already excluded); the helper
        # changes nothing for them.
        block = load_resource("zpa_segment_group")["block"]
        self.assertEqual(resource_input_attrs(block), classify_attributes(block))


class HclTypeTest(unittest.TestCase):
    def test_primitives(self):
        self.assertEqual(hcl_type("string"), "string")
        self.assertEqual(hcl_type("bool"), "bool")
        self.assertEqual(hcl_type("number"), "number")

    def test_collections(self):
        self.assertEqual(hcl_type(["set", "string"]), "set(string)")
        self.assertEqual(hcl_type(["list", "number"]), "list(number)")
        self.assertEqual(hcl_type(["map", "string"]), "map(string)")

    def test_object_collection_sorted_keys(self):
        t = ["list", ["object", {"to": "string", "from": "string"}]]
        self.assertEqual(
            hcl_type(t),
            "list(object({\n      from = optional(string)\n"
            "      to = optional(string)\n    }))",
        )

    def test_unknown_encoding_raises(self):
        with self.assertRaises(ValueError):
            hcl_type(["tuple", ["string"]])


class JsonSchemaTypeTest(unittest.TestCase):
    def test_primitives(self):
        self.assertEqual(json_schema_type("string"), {"type": "string"})
        self.assertEqual(json_schema_type("bool"), {"type": "boolean"})
        self.assertEqual(json_schema_type("number"), {"type": "number"})

    def test_collection(self):
        self.assertEqual(
            json_schema_type(["set", "string"]),
            {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
        )
        self.assertEqual(
            json_schema_type(["list", "string"]),
            {"type": "array", "items": {"type": "string"}},
        )

    def test_map(self):
        self.assertEqual(
            json_schema_type(["map", "string"]),
            {"type": "object", "additionalProperties": {"type": "string"}},
        )

    def test_object_collection(self):
        t = ["set", ["object", {"id": "number", "name": "string"}]]
        self.assertEqual(
            json_schema_type(t),
            {
                "type": "array",
                "uniqueItems": True,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "id": {"type": "number"},
                        "name": {"type": "string"},
                    },
                },
            },
        )


if __name__ == "__main__":
    unittest.main()
