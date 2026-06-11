"""Tests for tools/transform.py. All fixture data is fictional."""
import io
import json
import os
import sys
import unittest

from tools.transform import apply_overrides, coerce_item, derive_key, filter_item, load_override, render_imports, render_tfvars, slugify, snake, snake_keys, transform_items, _warn_if_slim
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

    def test_scalar_upgraded_to_collection(self):
        fake_block = {
            "attributes": {
                "ids": {"type": ["list", "number"], "optional": True},
                "names": {"type": ["set", "string"], "optional": True},
            }
        }
        item = {"ids": 10, "names": "solo"}
        self.assertEqual(
            coerce_item(item, fake_block), {"ids": [10], "names": ["solo"]}
        )


class OverrideTest(unittest.TestCase):
    def test_missing_override_is_empty(self):
        self.assertEqual(load_override("zpa_nonexistent_type"), {})

    def test_renames_and_drop_if_default(self):
        ov = {"renames": {"old_name": "new_name"}, "drop_if_default": {"flag": False}}
        item = {"old_name": "v", "flag": False, "keep": 1}
        self.assertEqual(apply_overrides(item, ov), {"new_name": "v", "keep": 1})

    def test_forced_reference(self):
        ov = {"references": {"server_groups": True}}
        item = {"server_groups": [{"id": "9", "name": "g"}]}
        self.assertEqual(apply_overrides(item, ov), {"server_groups": ["9"]})

    def test_split_csv_makes_real_lists(self):
        # ZCC returns list-typed settings as comma-joined strings.
        ov = {"split_csv": ["dns_server_ips"]}
        item = {"dns_server_ips": "10.0.0.53, 10.0.1.53"}
        self.assertEqual(
            apply_overrides(item, ov), {"dns_server_ips": ["10.0.0.53", "10.0.1.53"]}
        )

    def test_split_csv_empty_string_is_empty_list(self):
        ov = {"split_csv": ["ssids"]}
        self.assertEqual(apply_overrides({"ssids": ""}, ov), {"ssids": []})

    def test_split_csv_ignores_non_strings(self):
        ov = {"split_csv": ["already_list"]}
        self.assertEqual(
            apply_overrides({"already_list": ["a"]}, ov), {"already_list": ["a"]}
        )

    def test_split_csv_runs_after_renames(self):
        ov = {"renames": {"dns_servers": "dns_server_ips"},
              "split_csv": ["dns_server_ips"]}
        item = {"dns_servers": "1.1.1.1,2.2.2.2"}
        self.assertEqual(
            apply_overrides(item, ov), {"dns_server_ips": ["1.1.1.1", "2.2.2.2"]}
        )

    def test_unconditional_drops(self):
        ov = {"drops": ["noise_field"]}
        item = {"noise_field": "anything", "keep": 1}
        self.assertEqual(apply_overrides(item, ov), {"keep": 1})

    def test_drops_missing_field_is_noop(self):
        ov = {"drops": ["absent"]}
        self.assertEqual(apply_overrides({"keep": 1}, ov), {"keep": 1})


class DeriveKeyTest(unittest.TestCase):
    def test_default_name_slug(self):
        self.assertEqual(derive_key({"name": "Example Group A"}, {}), "example_group_a")

    def test_override_key_field(self):
        self.assertEqual(derive_key({"vanity_domain": "X-1"}, {"key_field": "vanity_domain"}), "x_1")

    def test_missing_key_field_raises(self):
        with self.assertRaises(KeyError):
            derive_key({"description": "no name"}, {})

    def test_composite_key_field(self):
        # names unique only within a type (cloud app control rules)
        item = {"type": "STREAMING_MEDIA", "name": "Block Risky"}
        self.assertEqual(
            derive_key(item, {"key_field": ["type", "name"]}),
            "streaming_media_block_risky",
        )

    def test_composite_key_missing_part_names_the_field(self):
        try:
            derive_key({"type": "WEBMAIL"}, {"key_field": ["type", "name"]})
            self.fail("expected KeyError")
        except KeyError as e:
            self.assertIn("name", str(e))


class PipelineTest(unittest.TestCase):
    RAW = [
        {"id": "2", "name": "B Group", "enabled": False, "applications": []},
        {
            "id": "1",
            "name": "A Group",
            "enabled": True,
            "creationTime": "1700000000",
            "applications": [{"id": 9, "name": "App"}],
        },
    ]

    def test_transform_items(self):
        items, originals, drops = transform_items(
            self.RAW, "zpa_segment_group", {}
        )
        self.assertEqual(sorted(items), ["a_group", "b_group"])
        self.assertEqual(items["a_group"]["applications"], [{"id": "9"}])
        self.assertNotIn("creation_time", items["a_group"])
        self.assertIn("creation_time", drops)
        self.assertEqual(originals["a_group"]["id"], "1")

    def test_duplicate_keys_raise(self):
        with self.assertRaises(ValueError):
            transform_items(
                [{"id": "1", "name": "Same"}, {"id": "2", "name": "same"}],
                "zpa_segment_group",
                {},
            )

    def test_render_imports_sorted_and_templated(self):
        originals = {"b": {"id": "20"}, "a": {"id": "10"}}
        text = render_imports("zpa_segment_group", originals, {})
        first = text.index('this["a"]')
        second = text.index('this["b"]')
        self.assertLess(first, second)
        self.assertIn('id = "10"', text)
        self.assertIn(
            'to = module.zpa_segment_group.zpa_segment_group.this["a"]', text
        )

    def test_import_id_template_multi_field(self):
        originals = {"a": {"id": "10", "type": "CUSTOM"}}
        text = render_imports("zia_fake", originals, {"import_id": "{type}:{id}"})
        self.assertIn('id = "CUSTOM:10"', text)


class AcknowledgedDropsTest(unittest.TestCase):
    def test_acknowledged_drops_suppressed_from_report(self):
        raw = [{"id": "1", "name": "A", "config_space": "X", "creation_time": "9"}]
        override = {"acknowledged_drops": ["config_space", "id"]}
        items, originals, drops = transform_items(raw, "zpa_segment_group", override)
        # acknowledged paths absent from the report...
        self.assertNotIn("config_space", drops)
        self.assertNotIn("id", drops)
        # ...but unacknowledged ones still surface
        self.assertIn("creation_time", drops)
        # and the field is still removed from the item regardless
        self.assertNotIn("config_space", items["a"])

    def test_no_acknowledged_drops_reports_all(self):
        raw = [{"id": "1", "name": "A", "config_space": "X"}]
        _, _, drops = transform_items(raw, "zpa_segment_group", {})
        self.assertIn("config_space", drops)
        self.assertIn("id", drops)


class SlimWarningTest(unittest.TestCase):
    def test_warns_on_slim_input(self):
        rs = load_resource("zia_url_categories")
        old = sys.stderr
        sys.stderr = io.StringIO()
        try:
            _warn_if_slim([{"id": "1"}, {"id": "2"}], rs["block"], "zia_url_categories")
            output = sys.stderr.getvalue()
        finally:
            sys.stderr = old
        self.assertIn("looks slim", output)

    def test_quiet_on_detail_input(self):
        rs = load_resource("zpa_segment_group")
        item = {"name": "x", "description": "d", "enabled": True, "microtenant_id": "1"}
        old = sys.stderr
        sys.stderr = io.StringIO()
        try:
            _warn_if_slim([item], rs["block"], "zpa_segment_group")
            output = sys.stderr.getvalue()
        finally:
            sys.stderr = old
        self.assertEqual(output, "")


class GoldenTransformTest(unittest.TestCase):
    def _roundtrip(self, resource_type):
        base = os.path.join(
            "tools", "tests", "fixtures", "transform", resource_type
        )
        with open(os.path.join(base, "api.json")) as f:
            raw = json.load(f)
        override = load_override(resource_type)
        items, originals, _ = transform_items(raw, resource_type, override)
        with open(os.path.join(base, "expected.auto.tfvars.json")) as f:
            self.assertEqual(render_tfvars(items), f.read())
        with open(os.path.join(base, "expected_imports.tf")) as f:
            self.assertEqual(
                render_imports(resource_type, originals, override), f.read()
            )

    def test_zpa_segment_group_golden(self):
        self._roundtrip("zpa_segment_group")

    def test_zia_url_categories_golden(self):
        self._roundtrip("zia_url_categories")

    def test_zpa_server_group_golden(self):
        self._roundtrip("zpa_server_group")

    def test_zpa_application_segment_golden(self):
        self._roundtrip("zpa_application_segment")

    def test_zia_location_management_golden(self):
        self._roundtrip("zia_location_management")

    def test_zia_ssl_inspection_rules_golden(self):
        self._roundtrip("zia_ssl_inspection_rules")

    def test_zia_cloud_app_control_rule_golden(self):
        self._roundtrip("zia_cloud_app_control_rule")


class SkipIfTest(unittest.TestCase):
    def test_matching_item_skipped_and_reported(self):
        raw = [
            {"id": "1", "name": "Default Rule", "defaultRule": True},
            {"id": "2", "name": "Custom Rule", "defaultRule": False},
        ]
        override = {"skip_if": [{"default_rule": True}]}
        old = sys.stderr
        sys.stderr = io.StringIO()
        try:
            items, originals, _ = transform_items(raw, "zpa_segment_group", override)
            err = sys.stderr.getvalue()
        finally:
            sys.stderr = old
        self.assertEqual(sorted(items), ["custom_rule"])
        self.assertNotIn("default_rule", items)
        self.assertIn("skipped", err)
        self.assertIn("Default Rule", err)

    def test_matcher_requires_all_pairs(self):
        raw = [{"id": "1", "name": "A", "predefined": True, "order": 5}]
        override = {"skip_if": [{"predefined": True, "order": -1}]}
        items, _, _ = transform_items(raw, "zpa_segment_group", override)
        self.assertIn("a", items)  # order!=-1 so the AND-matcher misses

    def test_no_skip_if_is_noop(self):
        raw = [{"id": "1", "name": "A"}]
        items, _, _ = transform_items(raw, "zpa_segment_group", {})
        self.assertIn("a", items)


if __name__ == "__main__":
    unittest.main()
