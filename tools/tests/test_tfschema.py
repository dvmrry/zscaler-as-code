"""Tests for tools/tfschema.py against the committed provider dumps."""
import unittest

from tools.tfschema import classify_attributes, load_resource


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


if __name__ == "__main__":
    unittest.main()
