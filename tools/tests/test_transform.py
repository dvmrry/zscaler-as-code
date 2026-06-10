"""Tests for tools/transform.py. All fixture data is fictional."""
import unittest

from tools.transform import coerce_item, filter_item, slugify, snake, snake_keys
from tools.tfschema import load_resource


class SnakeTest(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(snake("configSpace"), "config_space")
        self.assertEqual(snake("microtenantId"), "microtenant_id")

    def test_acronyms_and_capitalized(self):
        self.assertEqual(snake("dbCategorizedUrls"), "db_categorized_urls")
        self.assertEqual(snake("Type"), "type")
        self.assertEqual(snake("ScopeEntities"), "scope_entities")
        self.assertEqual(snake("tcpKeepAliveEnabled"), "tcp_keep_alive_enabled")

    def test_already_snake(self):
        self.assertEqual(snake("already_snake"), "already_snake")

    def test_snake_keys_recursive(self):
        data = {"configSpace": "X", "applications": [{"domainNames": ["a"]}]}
        self.assertEqual(
            snake_keys(data),
            {"config_space": "X", "applications": [{"domain_names": ["a"]}]},
        )


class SlugifyTest(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(slugify("Example Group A"), "example_group_a")

    def test_punctuation_collapses(self):
        self.assertEqual(slugify("Foo -- Bar (v2)"), "foo_bar_v2")

    def test_strips_edges(self):
        self.assertEqual(slugify("  spaced  "), "spaced")


class FilterTest(unittest.TestCase):
    def test_segment_group_filtering(self):
        rs = load_resource("zpa_segment_group")
        item = {
            "id": "1",
            "name": "A",
            "config_space": "DEFAULT",
            "policy_migrated": True,
            "applications": [
                {"id": "9", "name": "App", "domain_names": ["x"]}
            ],
        }
        drops = []
        out = filter_item(item, rs["block"], "", drops)
        self.assertEqual(
            out, {"name": "A", "applications": [{"id": "9"}]}
        )
        self.assertEqual(
            sorted(drops),
            [
                "applications[].domain_names",
                "applications[].name",
                "config_space",
                "id",
                "policy_migrated",
            ],
        )

    def test_single_block_dict_passthrough(self):
        rs = load_resource("zia_url_categories")
        item = {"url_keyword_counts": [{"total_url_count": 5}]}
        drops = []
        out = filter_item(item, rs["block"], "", drops)
        self.assertEqual(out, {"url_keyword_counts": [{"total_url_count": 5}]})
        self.assertEqual(drops, [])


class CoerceTest(unittest.TestCase):
    def test_primitive_coercions(self):
        rs = load_resource("zia_url_categories")
        item = {"custom_category": "true", "configured_name": 7}
        out = coerce_item(item, rs["block"])
        self.assertIs(out["custom_category"], True)
        self.assertEqual(out["configured_name"], "7")

    def test_number_from_string(self):
        fake_block = {"attributes": {"port": {"type": "number", "optional": True}}}
        self.assertEqual(coerce_item({"port": "443"}, fake_block), {"port": 443})

    def test_mechanical_ref_unwrap_scalar_and_list(self):
        fake_block = {
            "attributes": {
                "group_id": {"type": "number", "optional": True},
                "label_ids": {"type": ["set", "number"], "optional": True},
            }
        }
        item = {
            "group_id": {"id": 7, "name": "G"},
            "label_ids": [{"id": 1, "name": "a"}, {"id": 2}],
        }
        self.assertEqual(
            coerce_item(item, fake_block), {"group_id": 7, "label_ids": [1, 2]}
        )

    def test_blocks_recurse(self):
        rs = load_resource("zpa_segment_group")
        item = {"applications": [{"id": 123}]}
        out = coerce_item(item, rs["block"])
        self.assertEqual(out["applications"], [{"id": "123"}])


if __name__ == "__main__":
    unittest.main()
