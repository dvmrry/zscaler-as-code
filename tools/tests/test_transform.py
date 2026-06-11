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

    def test_list_block_passthrough(self):
        # forwarding_profile_actions is an UNBOUNDED list block (no
        # max_items); a list value is kept as a list of filtered dicts.
        rs = load_resource("zcc_forwarding_profile")
        item = {
            "forwarding_profile_actions": [{"action_type": 1}, {"action_type": 2}]
        }
        drops = []
        out = filter_item(item, rs["block"], "", drops)
        self.assertEqual(
            out,
            {"forwarding_profile_actions": [{"action_type": 1}, {"action_type": 2}]},
        )
        self.assertEqual(drops, [])

    def test_max_items_one_list_block_becomes_object(self):
        # url_keyword_counts is a max_items=1 LIST block: the API's
        # one-element list unwraps to a bare object (same single-instance
        # contract as nesting_mode=single).
        rs = load_resource("zia_url_categories")
        item = {"url_keyword_counts": [{"total_url_count": 5}]}
        drops = []
        out = filter_item(item, rs["block"], "", drops)
        self.assertEqual(out, {"url_keyword_counts": {"total_url_count": 5}})
        self.assertEqual(drops, [])

    def test_single_block_dict_stays_object(self):
        # system_proxy_data is nesting_mode=single, nested inside the
        # list-mode forwarding_profile_actions. Its dict value must stay a
        # bare object end-to-end (the generator wraps [x] at plan time), so
        # filter_item must NOT wrap it in a one-element list.
        rs = load_resource("zcc_forwarding_profile")
        item = {
            "forwarding_profile_actions": [
                {
                    "action_type": 1,
                    "system_proxy_data": {
                        "enable_proxy_server": True,
                        "proxy_server_address": "10.0.0.1",
                        "internal_noise": "drop me",
                    },
                }
            ]
        }
        drops = []
        out = filter_item(item, rs["block"], "", drops)
        self.assertEqual(
            out,
            {
                "forwarding_profile_actions": [
                    {
                        "action_type": 1,
                        "system_proxy_data": {
                            "enable_proxy_server": True,
                            "proxy_server_address": "10.0.0.1",
                        },
                    }
                ]
            },
        )
        # computed-only inner key dropped under a single-mode (no [] suffix) path
        self.assertEqual(
            drops,
            ["forwarding_profile_actions[].system_proxy_data.internal_noise"],
        )

    def test_single_block_legacy_list_unwrapped(self):
        # A one-element list for a single-mode block (odd/legacy API shape)
        # is unwrapped to the bare object.
        rs = load_resource("zcc_forwarding_profile")
        item = {
            "forwarding_profile_actions": [
                {"system_proxy_data": [{"enable_proxy_server": True}]}
            ]
        }
        drops = []
        out = filter_item(item, rs["block"], "", drops)
        self.assertEqual(
            out["forwarding_profile_actions"][0]["system_proxy_data"],
            {"enable_proxy_server": True},
        )
        self.assertEqual(drops, [])

    def test_single_block_multi_element_list_merged_with_conflict_report(self):
        # More than one element for a single-instance block merges
        # provider-style: scalar members keep the FIRST value, and a later
        # conflicting value is recorded in drops — reported, never silent.
        rs = load_resource("zcc_forwarding_profile")
        item = {
            "forwarding_profile_actions": [
                {"system_proxy_data": [{"enable_pac": True}, {"enable_pac": False}]}
            ]
        }
        drops = []
        out = filter_item(item, rs["block"], "", drops)
        self.assertEqual(
            out["forwarding_profile_actions"][0]["system_proxy_data"],
            {"enable_pac": True},
        )
        self.assertEqual(len(drops), 1)
        self.assertIn(
            "forwarding_profile_actions[].system_proxy_data.enable_pac", drops[0]
        )
        self.assertIn("conflicting", drops[0])

    def test_max_items_one_block_merges_id_group_elements(self):
        # The ZIA ID-group pattern: the API returns N {id, name} elements
        # for a max_items=1 set block whose only input member is id (a set
        # of numbers). The merge must union the ids into ONE object —
        # terraform core rejects a second block ("Too many ... blocks").
        rs = load_resource("zia_cloud_app_control_rule")
        item = {
            "departments": [
                {"id": 10, "name": "Engineering"},
                {"id": 20, "name": "Sales"},
                {"id": 30, "name": "Finance"},
            ]
        }
        drops = []
        out = filter_item(item, rs["block"], "", drops)
        self.assertEqual(out, {"departments": {"id": [10, 20, 30]}})
        # name is not an input member: dropped once via the schema filter,
        # with no per-element conflict noise.
        self.assertEqual(drops, ["departments.name"])


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

    def test_int_flags_coerce_to_bool(self):
        # ZCC returns flags as integers where the schema wants bool —
        # including TRI-STATE values like 2. The provider's own helper
        # (IntToBool) reads any non-zero as true; we mirror it exactly.
        fake_block = {
            "attributes": {
                "active": {"type": "bool", "optional": True},
                "enabled": {"type": "bool", "optional": True},
                "system_proxy": {"type": "bool", "optional": True},
                "count": {"type": "number", "optional": True},
            }
        }
        out = coerce_item(
            {"active": 1, "enabled": 0, "system_proxy": 2, "count": 1}, fake_block
        )
        self.assertIs(out["active"], True)
        self.assertIs(out["enabled"], False)
        self.assertIs(out["system_proxy"], True)  # tri-state non-zero -> true
        self.assertEqual(out["count"], 1)  # numbers untouched

    def test_blocks_recurse(self):
        rs = load_resource("zpa_segment_group")
        item = {"applications": [{"id": 123}]}
        out = coerce_item(item, rs["block"])
        self.assertEqual(out["applications"], [{"id": "123"}])

    def test_single_block_dict_coerces_in_place(self):
        # A single-mode block's value is a dict; coercion must recurse INTO
        # it (not pass it through), so int flags like the ZCC tri-state
        # coerce to bool inside the nested object.
        rs = load_resource("zcc_forwarding_profile")
        item = {
            "forwarding_profile_actions": [
                {
                    "action_type": 1,
                    "system_proxy_data": {
                        "enable_proxy_server": 2,  # tri-state int -> True
                        "enable_pac": 0,           # -> False
                        "proxy_server_port": 8080,  # schema string -> "8080"
                    },
                }
            ]
        }
        out = coerce_item(item, rs["block"])
        spd = out["forwarding_profile_actions"][0]["system_proxy_data"]
        self.assertIsInstance(spd, dict)
        self.assertIs(spd["enable_proxy_server"], True)
        self.assertIs(spd["enable_pac"], False)
        # proxy_server_port is schema type string; recursion coerces 8080 -> "8080"
        self.assertEqual(spd["proxy_server_port"], "8080")

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

    def test_empty_string_becomes_empty_collection(self):
        fake_block = {
            "attributes": {
                "ids": {"type": ["list", "number"], "optional": True},
                "names": {"type": ["set", "string"], "optional": True},
            }
        }
        out = coerce_item({"ids": "", "names": ""}, fake_block)
        self.assertEqual(out["ids"], [])
        self.assertEqual(out["names"], [])

    def test_object_typed_list_attr_members_coerce(self):
        # tcp_port_range/udp_port_range are object-typed list ATTRIBUTES
        # (not block_types): ["list", ["object", {"from": "string",
        # "to": "string"}]]. Members must coerce by their declared type the
        # same way block members do — an int/bool where the schema wants a
        # string must be stringified, mirroring quirk 6. Before the fix these
        # attributes passed through wholly uncoerced.
        rs = load_resource("zpa_application_segment")
        item = {"tcp_port_range": [{"from": 9002, "to": True}]}
        out = coerce_item(item, rs["block"])
        self.assertEqual(out["tcp_port_range"], [{"from": "9002", "to": "true"}])

    def test_object_typed_list_attr_drops_undeclared_member(self):
        # The generated HCL type is a strict object({...}), so an undeclared
        # member key fails `terraform plan`. Members absent from the schema
        # must be dropped, not passed through — the same treatment block
        # values get from filter_item.
        rs = load_resource("zpa_application_segment")
        item = {"tcp_port_range": [{"from": "443", "to": "443", "extra_field": "x"}]}
        out = coerce_item(item, rs["block"])
        self.assertEqual(out["tcp_port_range"], [{"from": "443", "to": "443"}])

    def test_object_typed_list_attr_ref_unwrap(self):
        # An object-typed list attribute whose member is a number must unwrap
        # {id,name} reference objects and coerce, exactly like a block member.
        fake_block = {
            "attributes": {
                "ranges": {
                    "type": ["list", ["object", {"port": "number"}]],
                    "optional": True,
                }
            }
        }
        item = {"ranges": [{"port": "443"}, {"port": {"id": 8080, "name": "x"}}]}
        out = coerce_item(item, fake_block)
        self.assertEqual(out["ranges"], [{"port": 443}, {"port": 8080}])


class OverrideTest(unittest.TestCase):
    def test_missing_override_is_empty(self):
        self.assertEqual(load_override("zpa_nonexistent_type"), {})

    def test_renames_and_drop_if_default(self):
        ov = {"renames": {"old_name": "new_name"}, "drop_if_default": {"flag": False}}
        item = {"old_name": "v", "flag": False, "keep": 1}
        self.assertEqual(apply_overrides(item, ov), {"new_name": "v", "keep": 1})

    def test_drop_if_default_coerces_numeric_string(self):
        # The API may hand back a number as a string (quirk 5). A non-divided
        # drop_if_default field like time_quota:'0' must still match the int
        # default 0 and drop, mirroring the divide step's own string-int
        # coercion.
        ov = {"drop_if_default": {"time_quota": 0}}
        self.assertEqual(apply_overrides({"time_quota": "0"}, ov), {})

    def test_drop_if_default_string_default_unaffected(self):
        # A string default (e.g. policy_style:'NONE') still compares directly;
        # the int-coercion branch must not perturb it.
        ov = {"drop_if_default": {"policy_style": "NONE"}}
        self.assertEqual(apply_overrides({"policy_style": "NONE"}, ov), {})
        self.assertEqual(
            apply_overrides({"policy_style": "REWRITE"}, ov),
            {"policy_style": "REWRITE"},
        )

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

    def test_divide_converts_units(self):
        # ZIA size_quota: API returns KB, the provider schema value is MB
        # (the provider does resp.SizeQuota / 1024 on read; its validator
        # range 10-100000 is in MB). 512000 KB -> 500 MB.
        ov = {"divide": {"size_quota": 1024}}
        self.assertEqual(
            apply_overrides({"size_quota": 512000}, ov), {"size_quota": 500}
        )

    def test_divide_handles_string_numbers(self):
        # API number-as-string still converts (and a string "0" lands on
        # int 0, so a following drop_if_default 0 catches it).
        ov = {"divide": {"size_quota": 1024}, "drop_if_default": {"size_quota": 0}}
        self.assertEqual(
            apply_overrides({"size_quota": "51200000"}, ov), {"size_quota": 50000}
        )
        self.assertEqual(apply_overrides({"size_quota": "0"}, ov), {})

    def test_divide_zero_still_drops(self):
        ov = {"divide": {"size_quota": 1024}, "drop_if_default": {"size_quota": 0}}
        self.assertEqual(apply_overrides({"size_quota": 0}, ov), {})

    def test_divide_leaves_non_numeric_untouched(self):
        ov = {"divide": {"size_quota": 1024}}
        self.assertEqual(
            apply_overrides({"size_quota": "unlimited"}, ov),
            {"size_quota": "unlimited"},
        )

    def test_zero_divisor_raises_with_file_path(self):
        # A 0 divisor would raise a bare ZeroDivisionError deep in
        # apply_overrides; load_override must catch it at load time and name
        # both the field and the override file so the fix is actionable.
        import tempfile
        import tools.transform as transform_mod

        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "zia_fake_div.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"divide": {"size_quota": 0}}, f)
        old_dir = transform_mod.OVERRIDES_DIR
        transform_mod.OVERRIDES_DIR = tmp
        try:
            transform_mod.load_override("zia_fake_div")
            self.fail("expected ValueError")
        except ValueError as e:
            self.assertIn("non-zero", str(e))
            self.assertIn("size_quota", str(e))
            self.assertIn(path, str(e))
        finally:
            transform_mod.OVERRIDES_DIR = old_dir
            os.remove(path)
            os.rmdir(tmp)

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

    def test_non_ascii_name_falls_back_to_id_key(self):
        # A name with NO ASCII-alphanumerics (e.g. CJK) slugs to '' on its
        # own; derive_key must fall back to a non-empty 'id_<id>' key so no
        # this[""] address is ever emitted.
        self.assertEqual(slugify("東京"), "")
        key = derive_key({"id": "42", "name": "東京"}, {})
        self.assertEqual(key, "id_42")

    def test_non_ascii_name_without_id_raises_with_remediation(self):
        try:
            derive_key({"name": "東京"}, {})
            self.fail("expected ValueError")
        except ValueError as e:
            self.assertIn("key_field", str(e))


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

    def test_id_group_blocks_and_quota_defaults_through_pipeline(self):
        # A realistic ZIA rule: camelCase keys, multi-element ID-group
        # blocks, and sizeQuota/timeQuota 0 meaning "not set" (a provider
        # runtime validator rejects 0, so the override drops it).
        raw = [
            {
                "id": 101,
                "type": "STREAMING_MEDIA",
                "name": "Block big streams",
                "order": 1,
                "sizeQuota": 0,
                "timeQuota": 0,
                "departments": [
                    {"id": 10, "name": "Engineering"},
                    {"id": 20, "name": "Sales"},
                ],
                "groups": [{"id": 7, "name": "All"}],
            },
            {
                "id": 102,
                "type": "STREAMING_MEDIA",
                "name": "Large file quota",
                "order": 2,
                "sizeQuota": 102400000,
                "timeQuota": 0,
            },
        ]
        override = {
            "key_field": ["type", "name"],
            "divide": {"size_quota": 1024},
            "drop_if_default": {"size_quota": 0, "time_quota": 0},
        }
        items, originals, drops = transform_items(
            raw, "zia_cloud_app_control_rule", override
        )
        item = items["streaming_media_block_big_streams"]
        self.assertEqual(item["departments"], {"id": [10, 20]})
        self.assertEqual(item["groups"], {"id": [7]})
        self.assertNotIn("size_quota", item)
        self.assertNotIn("time_quota", item)
        # 102400000 KB from the API -> 100000 MB in config (the provider
        # validator's exact ceiling — a real 100GB tenant rule).
        quota_item = items["streaming_media_large_file_quota"]
        self.assertEqual(quota_item["size_quota"], 100000)

    def test_string_zero_time_quota_drops_through_pipeline(self):
        # time_quota is in drop_if_default but NOT divided, so before the fix
        # an API number-as-string timeQuota:'0' survived as an explicit
        # time_quota=0 (plan drift). It must now drop the same way the int 0
        # case does.
        raw = [{
            "id": "5",
            "name": "rule1",
            "timeQuota": "0",
            "sizeQuota": "0",
        }]
        override = load_override("zia_url_filtering_rules")
        items, _, _ = transform_items(raw, "zia_url_filtering_rules", override)
        item = items["rule1"]
        self.assertNotIn("time_quota", item)
        self.assertNotIn("size_quota", item)

    def test_duplicate_keys_raise(self):
        with self.assertRaises(ValueError):
            transform_items(
                [{"id": "1", "name": "Same"}, {"id": "2", "name": "same"}],
                "zpa_segment_group",
                {},
            )

    def test_two_non_ascii_names_transform_without_empty_key(self):
        # Two distinct CJK-named items both slug to '' on their name alone;
        # the id fallback gives each a distinct non-empty key, so the
        # pipeline neither raises a duplicate-'' ValueError nor emits a
        # this[""] address.
        raw = [
            {"id": "1", "name": "東京"},
            {"id": "2", "name": "大阪"},
        ]
        items, originals, _ = transform_items(raw, "zpa_segment_group", {})
        self.assertEqual(sorted(items), ["id_1", "id_2"])
        self.assertNotIn("", items)

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


class MovedBlocksTest(unittest.TestCase):
    OLD = (
        'import {\n'
        '  to = module.zia_rule_labels.zia_rule_labels.this["old_name"]\n'
        '  id = "101"\n'
        '}\n\n'
        'import {\n'
        '  to = module.zia_rule_labels.zia_rule_labels.this["stable"]\n'
        '  id = "102"\n'
        '}\n'
    )

    def test_parse_import_pairs(self):
        from tools.transform import parse_import_pairs
        self.assertEqual(
            parse_import_pairs(self.OLD), {"old_name": "101", "stable": "102"}
        )

    def test_rename_detected_same_id_new_key(self):
        from tools.transform import derive_moves
        new = self.OLD.replace("old_name", "new_name")
        self.assertEqual(derive_moves(self.OLD, new), [("old_name", "new_name")])

    def test_add_and_remove_are_not_renames(self):
        from tools.transform import derive_moves
        # 101 removed entirely; 103 added: neither is a rename.
        new = (
            'import {\n'
            '  to = module.zia_rule_labels.zia_rule_labels.this["stable"]\n'
            '  id = "102"\n'
            '}\n\n'
            'import {\n'
            '  to = module.zia_rule_labels.zia_rule_labels.this["brand_new"]\n'
            '  id = "103"\n'
            '}\n'
        )
        self.assertEqual(derive_moves(self.OLD, new), [])

    def test_composite_import_id_renames(self):
        from tools.transform import derive_moves
        old = (
            'import {\n'
            '  to = module.zia_cloud_app_control_rule.zia_cloud_app_control_rule.this["streaming_media_old"]\n'
            '  id = "STREAMING_MEDIA:55"\n'
            '}\n'
        )
        new = old.replace("streaming_media_old", "streaming_media_new")
        self.assertEqual(
            derive_moves(old, new),
            [("streaming_media_old", "streaming_media_new")],
        )

    def test_render_moves_addresses(self):
        from tools.transform import render_moves
        out = render_moves("zia_rule_labels", [("a", "b")])
        self.assertIn('from = module.zia_rule_labels.zia_rule_labels.this["a"]', out)
        self.assertIn('to   = module.zia_rule_labels.zia_rule_labels.this["b"]', out)
        self.assertTrue(out.startswith("moved {"))


class MovedBlocksEndToEndTest(unittest.TestCase):
    TENANT = "tmpmovestest"

    def test_rename_between_transforms_stages_moves_file(self):
        import shutil
        import tempfile
        from tools.transform import main as transform_main

        self.addCleanup(shutil.rmtree, os.path.join("config", self.TENANT), True)
        self.addCleanup(shutil.rmtree, os.path.join("imports", self.TENANT), True)
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "in.json")
            with open(src, "w", encoding="utf-8") as f:
                json.dump([{"id": 7, "name": "Original Name"}], f)
            self.assertEqual(
                transform_main(["zia_rule_labels", src, self.TENANT]), 0)
            moves_path = os.path.join(
                "imports", self.TENANT, "zia_rule_labels_moves.tf")
            self.assertFalse(os.path.exists(moves_path), "no rename yet")
            # the console rename: same id, new name -> new derived key
            with open(src, "w", encoding="utf-8") as f:
                json.dump([{"id": 7, "name": "Renamed Thing"}], f)
            self.assertEqual(
                transform_main(["zia_rule_labels", src, self.TENANT]), 0)
            self.assertTrue(os.path.exists(moves_path))
            with open(moves_path, encoding="utf-8") as f:
                body = f.read()
            self.assertIn('from = module.zia_rule_labels.zia_rule_labels.this["original_name"]', body)
            self.assertIn('to   = module.zia_rule_labels.zia_rule_labels.this["renamed_thing"]', body)


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
        with open(os.path.join(base, "api.json"), encoding="utf-8") as f:
            raw = json.load(f)
        override = load_override(resource_type)
        items, originals, _ = transform_items(raw, resource_type, override)
        with open(os.path.join(base, "expected.auto.tfvars.json"), encoding="utf-8") as f:
            self.assertEqual(render_tfvars(items), f.read())
        with open(os.path.join(base, "expected_imports.tf"), encoding="utf-8") as f:
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
