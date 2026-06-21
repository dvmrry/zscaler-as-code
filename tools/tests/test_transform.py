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

    def test_nested_type_object_attr_members_coerce(self):
        # Plugin-framework nested_type attributes are object attributes, not
        # block_types. They still need recursive member coercion and strict
        # object-member filtering.
        fake_block = {
            "attributes": {
                "settings": {
                    "nested_type": {
                        "nesting_mode": "single",
                        "attributes": {
                            "enabled": {"type": "bool", "optional": True},
                            "port": {"type": "number", "optional": True},
                            "label": {"type": "string", "computed": True},
                        },
                    },
                    "optional": True,
                }
            }
        }
        out = coerce_item(
            {"settings": {"enabled": "1", "port": {"id": "443"}, "label": "drop"}},
            fake_block,
        )
        self.assertEqual(out, {"settings": {"enabled": True, "port": 443}})


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

    def test_defaults_fill_absent_and_empty(self):
        # ZIA "ANY category" rules: GET omits/empties urlCategories, the
        # write API rejects an empty list, and the provider's own read
        # normalizes empty to ["ANY"] — the canonical stable value.
        ov = {"defaults": {"url_categories": ["ANY"]}}
        self.assertEqual(
            apply_overrides({"name": "r"}, ov),
            {"name": "r", "url_categories": ["ANY"]},
        )
        self.assertEqual(
            apply_overrides({"name": "r", "url_categories": []}, ov),
            {"name": "r", "url_categories": ["ANY"]},
        )
        self.assertEqual(
            apply_overrides({"name": "r", "url_categories": None}, ov),
            {"name": "r", "url_categories": ["ANY"]},
        )

    def test_defaults_leave_real_values(self):
        ov = {"defaults": {"url_categories": ["ANY"]}}
        self.assertEqual(
            apply_overrides({"url_categories": ["NEWS_AND_MEDIA"]}, ov),
            {"url_categories": ["NEWS_AND_MEDIA"]},
        )

    def test_defaults_are_deep_copied_per_item(self):
        # A shared mutable default would let one item's later mutation
        # bleed into every other item.
        ov = {"defaults": {"url_categories": ["ANY"]}}
        a = apply_overrides({"name": "a"}, ov)
        b = apply_overrides({"name": "b"}, ov)
        a["url_categories"].append("MUTATED")
        self.assertEqual(b["url_categories"], ["ANY"])

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

    def test_singleton_default_id_keys_and_imports_but_not_config(self):
        raw = [{"maliciousUrls": ["bad.example"]}]
        override = load_override("zia_atp_malicious_urls")
        items, originals, drops = transform_items(
            raw, "zia_atp_malicious_urls", override
        )
        self.assertEqual(sorted(items), ["all_urls"])
        self.assertEqual(items["all_urls"], {"malicious_urls": ["bad.example"]})
        self.assertEqual(originals["all_urls"]["id"], "all_urls")
        self.assertEqual(drops, [])
        self.assertIn(
            'id = "all_urls"',
            render_imports("zia_atp_malicious_urls", originals, override),
        )

    def test_dlp_predefined_engine_name_feeds_required_name(self):
        raw = [{
            "id": "7",
            "predefinedEngineName": "Predefined PCI",
            "customDlpEngine": False,
        }]
        items, _, drops = transform_items(
            raw, "zia_dlp_engines", load_override("zia_dlp_engines")
        )
        self.assertEqual(sorted(items), ["predefined_pci"])
        self.assertEqual(items["predefined_pci"]["name"], "Predefined PCI")
        self.assertEqual(drops, [])

    def test_url_cloud_app_prompt_acronym_renames(self):
        raw = [{
            "id": "app_setting",
            "enableChatGptPrompt": True,
            "enableMicrosoftCoPilotPrompt": True,
            "enablePerplexityPrompt": True,
            "enableDeepseekPrompt": True,
            "enablePoEPrompt": True,
            "enableUcaasLogMeIn": True,
        }]
        items, _, drops = transform_items(
            raw,
            "zia_url_filtering_and_cloud_app_settings",
            load_override("zia_url_filtering_and_cloud_app_settings"),
        )
        item = items["app_setting"]
        self.assertTrue(item["enable_chatgpt_prompt"])
        self.assertTrue(item["enable_microsoft_copilot_prompt"])
        self.assertTrue(item["enable_per_plexity_prompt"])
        self.assertTrue(item["enable_deep_seek_prompt"])
        self.assertTrue(item["enable_poe_prompt"])
        self.assertTrue(item["enable_ucaas_logmein"])
        self.assertEqual(drops, [])

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


class NullObjectStubTest(unittest.TestCase):
    """The ZIA/ZPA "not configured" stubs: blocks the API emits with id=0
    that the providers' own flatteners treat as absent (field-hit on
    location extranets, cbi_profile, server_groups). Config must drop
    them or adoption plans show perpetual phantom diffs."""

    def test_extranet_stubs_dropped_from_location(self):
        rs = load_resource("zia_location_management")
        item = {
            "name": "HQ",
            "extranet": {"id": 0},
            "extranet_dns": [{"id": 0}],
            "extranet_ip_pool": {"id": 0},
        }
        drops = []
        out = filter_item(item, rs["block"], "", drops)
        self.assertEqual(out.get("extranet"), [])
        self.assertEqual(out.get("extranet_dns"), [])
        self.assertEqual(out.get("extranet_ip_pool"), [])
        self.assertEqual(drops, [])

    def test_real_extranet_kept(self):
        rs = load_resource("zia_location_management")
        item = {"extranet": [{"id": 42}]}
        out = filter_item(item, rs["block"], "", [])
        self.assertEqual(out["extranet"], [{"id": 42}])

    def test_cbi_profile_stub_element_dropped(self):
        rs = load_resource("zia_url_filtering_rules")
        item = {"cbi_profile": [
            {"id": "0", "name": "", "url": "", "profile_seq": 0}
        ]}
        out = filter_item(item, rs["block"], "", [])
        self.assertEqual(out["cbi_profile"], [])

    def test_server_groups_stub_dropped_real_kept(self):
        rs = load_resource("zpa_application_segment")
        item = {"server_groups": [{"id": 0}, {"id": "216199"}]}
        out = filter_item(item, rs["block"], "", [])
        self.assertEqual(out["server_groups"], [{"id": "216199"}])

    def test_bool_member_marks_block_real(self):
        from tools.transform import _is_null_object
        self.assertFalse(_is_null_object({"id": 0, "enabled": False}))


class PredefinedUrlFilteringSkipTest(unittest.TestCase):
    def test_one_click_url_rules_skipped(self):
        # One Click provisions service-managed URL FILTERING rules too
        # (not just SSL): Zscaler re-asserts their positions, so managing
        # them fights the service — order churns on every read. Same skip
        # as ssl_inspection, via the real override file.
        from tools.transform import load_override, transform_items

        raw = [
            {"id": 1, "name": "Office 365 One Click Rule", "order": 1,
             "predefined": True, "protocols": ["ANY_RULE"]},
            {"id": 2, "name": "Custom Deny", "order": 2,
             "predefined": False, "protocols": ["ANY_RULE"]},
        ]
        ov = load_override("zia_url_filtering_rules")
        items, originals, _ = transform_items(
            raw, "zia_url_filtering_rules", ov)
        self.assertEqual(sorted(items), ["custom_deny"])


class QuirkClosureTest(unittest.TestCase):
    """Survey-verified gap closures (provider-source-mined), one e2e each."""

    def test_failopen_inverted_bools(self):
        # ZCC failopen API: 0 = ENABLED (provider boolToInvertedInt).
        from tools.transform import load_override, transform_items

        raw = [{"id": 9, "enableFailOpen": 0, "active": "0",
                "enableCaptivePortalDetection": 1}]
        ov = load_override("zcc_failopen_policy")
        items, _, _ = transform_items(raw, "zcc_failopen_policy", ov)
        it = items["9"]
        self.assertIs(it["enable_fail_open"], True)
        self.assertIs(it["active"], True)
        self.assertIs(it["enable_captive_portal_detection"], False)

    def test_policy_access_rule_drops_and_merges(self):
        from tools.transform import load_override, transform_items

        raw = [{"id": "r1", "name": "Rule", "priority": "3",
                "ruleOrder": "3", "microtenantId": "0",
                "appServerGroups": [{"id": "s1"}, {"id": "s2"}]}]
        ov = load_override("zpa_policy_access_rule")
        items, _, _ = transform_items(raw, "zpa_policy_access_rule", ov)
        it = items["rule"]
        self.assertNotIn("priority", it)
        self.assertNotIn("rule_order", it)
        self.assertNotIn("microtenant_id", it)
        self.assertEqual(it["app_server_groups"], [{"id": ["s1", "s2"]}])

    def test_policy_style_value_map(self):
        from tools.transform import load_override, transform_items

        raw = [{"id": "s", "name": "Seg", "domainNames": ["a.test"],
                "policyStyle": "DUAL_POLICY_EVAL"}]
        ov = load_override("zpa_application_segment")
        items, _, _ = transform_items(raw, "zpa_application_segment", ov)
        self.assertIs(items["seg"]["policy_style"], True)

    def test_source_countries_prefix_stripped(self):
        from tools.transform import load_override, transform_items

        raw = [{"id": 1, "name": "R", "order": 1, "protocols": ["ANY_RULE"],
                "sourceCountries": ["COUNTRY_US", "COUNTRY_CA"]}]
        ov = load_override("zia_url_filtering_rules")
        items, _, _ = transform_items(raw, "zia_url_filtering_rules", ov)
        # source_countries is a SET — canonicalized to sorted order (CA before
        # US) so a re-fetch in a different API order doesn't churn the config.
        self.assertEqual(items["r"]["source_countries"], ["CA", "US"])

    def test_set_typed_fields_are_canonically_sorted(self):
        from tools.transform import coerce_item

        block = {"attributes": {
            "keywords": {"type": ["set", "string"]},   # set -> sort
            "urls": {"type": ["list", "string"]},       # list -> keep order
        }}
        out = coerce_item(
            {"keywords": ["zebra", "apple", "mango"],
             "urls": ["z.example", "a.example"]}, block)
        self.assertEqual(out["keywords"], ["apple", "mango", "zebra"])
        self.assertEqual(out["urls"], ["z.example", "a.example"])

    def test_predefined_cloud_app_rules_skipped(self):
        from tools.transform import load_override, transform_items

        raw = [
            {"id": 1, "type": "STREAMING_MEDIA", "name": "One Click",
             "order": 1, "predefined": True},
            {"id": 2, "type": "STREAMING_MEDIA", "name": "Custom",
             "order": 2, "predefined": False},
        ]
        ov = load_override("zia_cloud_app_control_rule")
        items, _, _ = transform_items(raw, "zia_cloud_app_control_rule", ov)
        self.assertEqual(sorted(items), ["streaming_media_custom"])

    def test_signing_cert_renamed_to_enrollment_cert(self):
        # OAuth2-migrated ZPA tenants REQUIRE the connector signing cert
        # on app connector group writes; the API names it signingCertId,
        # the provider schema names it enrollment_cert_id (zpa#650). The
        # provider auto-resolves it on CREATE only — updates send exactly
        # what config carries, so config must carry it.
        from tools.transform import load_override, transform_items

        ov = load_override("zpa_app_connector_group")
        raw = [{"id": "1", "name": "ACG", "signingCertId": "9001"}]
        items, _, _ = transform_items(raw, "zpa_app_connector_group", ov)
        self.assertEqual(items["acg"]["enrollment_cert_id"], "9001")

        # tenants whose GET already speaks the schema name keep working
        raw = [{"id": "2", "name": "ACG2", "enrollmentCertId": "9002"}]
        items, _, _ = transform_items(raw, "zpa_app_connector_group", ov)
        self.assertEqual(items["acg2"]["enrollment_cert_id"], "9002")

        # absent stays absent (provider auto-resolves on create)
        raw = [{"id": "3", "name": "ACG3"}]
        items, _, _ = transform_items(raw, "zpa_app_connector_group", ov)
        self.assertNotIn("enrollment_cert_id", items["acg3"])

    def test_zpa_microtenant_stub_dropped_everywhere(self):
        # Survey finding: only policy_access_rule had the "0" stub drop;
        # the audit closed the other five ZPA resources. Real ids survive.
        from tools.transform import load_override, transform_items

        raw = [{"id": "a", "name": "A", "domainNames": ["a.test"],
                "microtenantId": "0"},
               {"id": "b", "name": "B", "domainNames": ["b.test"],
                "microtenantId": "7"}]
        ov = load_override("zpa_application_segment")
        items, _, _ = transform_items(raw, "zpa_application_segment", ov)
        self.assertNotIn("microtenant_id", items["a"])
        self.assertEqual(items["b"]["microtenant_id"], "7")
        for rt in ("zpa_app_connector_group", "zpa_application_server",
                   "zpa_server_group", "zpa_segment_group"):
            self.assertEqual(
                load_override(rt).get("drop_if_default", {}).get(
                    "microtenant_id"), "0", rt)

    def test_default_microtenant_controller_is_skipped(self):
        # DAV-30 bootstrap finding: the API lists the Default microtenant
        # as id "0", but the provider cannot import that system object.
        from tools.transform import load_override, transform_items

        raw = [
            {"id": "0", "name": "Default"},
            {"id": "1", "name": "Microtenant"},
        ]
        ov = load_override("zpa_microtenant_controller")
        items, originals, drops = transform_items(
            raw, "zpa_microtenant_controller", ov)
        self.assertEqual(sorted(items), ["microtenant"])
        self.assertEqual(sorted(originals), ["microtenant"])
        self.assertEqual(drops, [])

    def test_url_category_urls_sorted(self):
        # zia suppressURLCategoriesReorderDiff treats urls as a SET at
        # plan time; the API returns unstable order. Sorting makes
        # re-fetches byte-stable (plan-invisible: provider absorbs it).
        from tools.transform import load_override, transform_items

        raw = [{"id": "CUSTOM_01", "configuredName": "Cat",
                "superCategory": "USER_DEFINED",
                "urls": ["zeta.test", "alpha.test", "mid.test"]}]
        ov = load_override("zia_url_categories")
        items, _, _ = transform_items(raw, "zia_url_categories", ov)
        self.assertEqual(items["cat"]["urls"],
                         ["alpha.test", "mid.test", "zeta.test"])

    def test_zpa_html_entities_unescaped(self):
        # The Go SDK unescapes TOP-LEVEL name/description on every ZPA/ZCC
        # response, applied twice (zscaler-sdk-go v3.8.37 unescapeHTML), so
        # provider state holds the literal characters while the raw API
        # carries entities — config must mirror or plans show phantom
        # updates (&amp;, &gt;). Other fields and ZIA are untouched.
        from tools.transform import (
            _unescape_html_fields, load_override, transform_items,
        )

        raw = [{"id": "s", "name": "R&amp;amp;D &gt; Segment",
                "description": "a &amp; b",
                "domainNames": ["x&amp;y.test"]}]
        ov = load_override("zpa_application_segment")
        items, _, _ = transform_items(raw, "zpa_application_segment", ov)
        self.assertEqual(sorted(items), ["r_d_segment"])
        it = items["r_d_segment"]
        self.assertEqual(it["name"], "R&D > Segment")
        self.assertEqual(it["description"], "a & b")
        # non-name/description fields keep the API's escaped form
        self.assertEqual(it["domain_names"], ["x&amp;y.test"])

        zcc = {"name": "&amp;", "policy_name": "&amp;"}
        _unescape_html_fields(zcc, "zcc_forwarding_profile")
        self.assertEqual(zcc["name"], "&")
        self.assertEqual(zcc["policy_name"], "&amp;")

        zia = {"name": "A &amp; B"}
        _unescape_html_fields(zia, "zia_url_filtering_rules")
        self.assertEqual(zia["name"], "A &amp; B")

    def test_policy_access_custom_msg_html_escaped(self):
        # zpa_policy_access_rule.custom_msg is the opposite of the normal
        # ZPA name/description path: provider read-back/state carries Go's
        # HTML-escaped string, so config copied from raw API pulls must
        # escape this field or import/bootstrap plans want to rewrite it.
        from tools.transform import load_override, transform_items

        raw = [
            {
                "id": "1",
                "name": "Raw apostrophe",
                "customMsg": "Contact your organization's admin & security",
            },
            {
                "id": "2",
                "name": "Already escaped",
                "customMsg": "Contact your organization&#39;s admin &amp; security",
            },
        ]
        ov = load_override("zpa_policy_access_rule")
        items, _, drops = transform_items(raw, "zpa_policy_access_rule", ov)
        expected = "Contact your organization&#39;s admin &amp; security"
        self.assertEqual(items["raw_apostrophe"]["custom_msg"], expected)
        self.assertEqual(items["already_escaped"]["custom_msg"], expected)
        self.assertEqual(drops, [])

    def test_operand_drift_fields_dropped(self):
        # zpa#287: operands.name is Computed+Optional — the API rewrites it
        # to the referenced object's display name, so a config copy can
        # never round-trip; the maintainer's fix is "remove name from your
        # operands". Nested microtenant_id "0" is the default-tenant stub
        # (same rule as the top-level drop_if_default). Both reach inside
        # conditions[].operands[] via dotted override paths. Item-level
        # name (the key source) must be untouched.
        from tools.transform import load_override, transform_items

        raw = [{
            "id": "r1", "name": "Rule", "microtenantId": "0",
            "conditions": [
                {"id": "c1", "operator": "OR", "microtenantId": "0",
                 "operands": [
                     {"id": "o1", "objectType": "APP", "lhs": "id",
                      "rhs": "111", "name": "Display Name",
                      "microtenantId": "0"},
                     {"id": "o2", "objectType": "SCIM_GROUP",
                      "lhs": "216196257331285825", "rhs": "3251059",
                      "name": "Engineering", "idpId": "216196257331285825",
                      "microtenantId": "9999"},
                 ]},
                {"id": "c2", "operator": "AND", "microtenantId": "8888",
                 "operands": [{"id": "o3", "objectType": "APP", "lhs": "id",
                               "rhs": "222"}]},
            ],
        }]
        ov = load_override("zpa_policy_access_rule")
        items, _, _ = transform_items(raw, "zpa_policy_access_rule", ov)
        it = items["rule"]
        self.assertEqual(it["name"], "Rule")
        ops = it["conditions"][0]["operands"]
        self.assertNotIn("name", ops[0])
        self.assertNotIn("name", ops[1])
        self.assertNotIn("microtenant_id", ops[0])
        self.assertNotIn("microtenant_id", it["conditions"][0])
        # REAL (non-"0") nested microtenant ids must survive at both levels
        self.assertEqual(ops[1]["microtenant_id"], "9999")
        self.assertEqual(it["conditions"][1]["microtenant_id"], "8888")
        self.assertEqual(ops[1]["idp_id"], "216196257331285825")
        # operand order is preserved verbatim (TypeList semantics)
        self.assertEqual([o["rhs"] for o in ops], ["111", "3251059"])


class MergeBlocksTest(unittest.TestCase):
    """The schema-lies-flatten-merges class: zpa declares plain list
    blocks but its READ collapses all API elements into ONE block with
    merged id sets (provider source, v4.4.4). Field-hit: a segment with
    2+ server groups showed phantom diffs at import; single-group
    segments matched by accident."""

    def test_multi_server_group_segment_merges_to_one_block(self):
        from tools.transform import load_override, transform_items

        raw = [{
            "id": "seg1", "name": "Multi", "domainNames": ["a.test"],
            "serverGroups": [
                {"id": "g111", "name": "one"},
                {"id": "g222", "name": "two"},
            ],
        }]
        ov = load_override("zpa_application_segment")
        items, _, _ = transform_items(raw, "zpa_application_segment", ov)
        self.assertEqual(
            items["multi"]["server_groups"], [{"id": ["g111", "g222"]}])

    def test_single_group_shape_unchanged(self):
        from tools.transform import load_override, transform_items

        raw = [{"id": "s", "name": "One", "domainNames": ["a.test"],
                "serverGroups": [{"id": "g111"}]}]
        ov = load_override("zpa_application_segment")
        items, _, _ = transform_items(raw, "zpa_application_segment", ov)
        self.assertEqual(items["one"]["server_groups"], [{"id": ["g111"]}])

    def test_server_group_connector_groups_merge(self):
        from tools.transform import load_override, transform_items

        raw = [{"id": "sg", "name": "SG", "appConnectorGroups": [
            {"id": "c1"}, {"id": "c2"}], "applications": [
            {"id": "a1"}, {"id": "a2"}]}]
        ov = load_override("zpa_server_group")
        items, _, _ = transform_items(raw, "zpa_server_group", ov)
        self.assertEqual(items["sg"]["app_connector_groups"], [{"id": ["c1", "c2"]}])
        self.assertEqual(items["sg"]["applications"], [{"id": ["a1", "a2"]}])

    def test_segment_group_applications_dropped(self):
        # Survey-verified: segment_group applications is a server-computed
        # BACK-reference (membership is managed from the segment side);
        # carrying it invites phantom diffs — dropped via override.
        from tools.transform import load_override, transform_items

        raw = [{"id": "x", "name": "G", "applications": [
            {"id": "a1"}, {"id": "a2"}]}]
        ov = load_override("zpa_segment_group")
        items, _, _ = transform_items(raw, "zpa_segment_group", ov)
        self.assertNotIn("applications", items["g"])


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

    def test_stale_moves_file_removed_when_a_later_run_has_no_rename(self):
        # DAV-8 P2: a rename stages _moves.tf; a subsequent run with no
        # rename must REMOVE it, so transform output never depends on a
        # prior run (else stale moved blocks get staged into env roots).
        import shutil
        import tempfile
        from tools.transform import main as transform_main

        self.addCleanup(shutil.rmtree, os.path.join("config", self.TENANT), True)
        self.addCleanup(shutil.rmtree, os.path.join("imports", self.TENANT), True)
        moves_path = os.path.join(
            "imports", self.TENANT, "zia_rule_labels_moves.tf")
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "in.json")
            # run 1: baseline
            with open(src, "w", encoding="utf-8") as f:
                json.dump([{"id": 7, "name": "Original Name"}], f)
            self.assertEqual(
                transform_main(["zia_rule_labels", src, self.TENANT]), 0)
            # run 2: rename -> moves file staged
            with open(src, "w", encoding="utf-8") as f:
                json.dump([{"id": 7, "name": "Renamed Thing"}], f)
            self.assertEqual(
                transform_main(["zia_rule_labels", src, self.TENANT]), 0)
            self.assertTrue(os.path.exists(moves_path), "rename should stage moves")
            # run 3: same data, no rename -> stale moves file must be gone
            self.assertEqual(
                transform_main(["zia_rule_labels", src, self.TENANT]), 0)
            self.assertFalse(
                os.path.exists(moves_path),
                "stale moves file must be removed when a run has no rename")


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
        raw = [{"id": "1", "name": "A", "config_space": "X", "creation_time": "9"}]
        _, _, drops = transform_items(raw, "zpa_segment_group", {})
        self.assertIn("config_space", drops)
        self.assertIn("creation_time", drops)
        self.assertNotIn("id", drops)


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



class LoudFailureTest(unittest.TestCase):
    """Error paths must name the problem and the next command."""

    def test_main_rejects_non_list_input(self):
        import io, sys, tempfile, os, json as _json
        from tools.transform import main
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "in.json")
            with open(src, "w", encoding="utf-8") as f:
                _json.dump({"list": [], "pageInfo": {}}, f)
            old_err, sys.stderr = sys.stderr, io.StringIO()
            try:
                code = main(["zpa_segment_group", src, "tmpxform"])
                err = sys.stderr.getvalue()
            finally:
                sys.stderr = old_err
        self.assertEqual(code, 2)
        self.assertIn("JSON LIST", err)
        self.assertIn("make fetch", err)

    def test_render_imports_names_override_on_missing_field(self):
        from tools.transform import render_imports
        with self.assertRaises(ValueError) as ctx:
            render_imports("zia_rule_labels", {"k": {"id": "1"}},
                           {"import_id": "{type}:{id}"})
        msg = str(ctx.exception)
        self.assertIn("tools/overrides/zia_rule_labels.json", msg)
        self.assertIn("'k'", msg)


class OverrideAuthoringValidationTest(unittest.TestCase):
    """load_override rejects the silent-no-op authoring traps at load
    time, naming the file (mirrors the divide-by-zero validation)."""

    def _load_with(self, data, rt="zpa_segment_group"):
        import json as _json
        import tempfile
        import tools.transform as transform_mod
        from tools.transform import load_override
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, rt + ".json"), "w",
                      encoding="utf-8") as f:
                _json.dump(data, f)
            old_dir = transform_mod.OVERRIDES_DIR
            transform_mod.OVERRIDES_DIR = tmp
            try:
                return load_override(rt)
            finally:
                transform_mod.OVERRIDES_DIR = old_dir

    def test_drops_using_pre_rename_name_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self._load_with({"renames": {"oldname": "newname"},
                             "drops": ["oldname"]})
        self.assertIn("pre-rename", str(ctx.exception))

    def test_dotted_sort_lists_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self._load_with({"sort_lists": ["conditions.urls"]})
        self.assertIn("nested", str(ctx.exception))

    def test_dotted_drop_path_must_resolve_in_schema(self):
        with self.assertRaises(ValueError) as ctx:
            self._load_with({"drops": ["conditions.operands.nope"]},
                            rt="zpa_policy_access_rule")
        self.assertIn("not an attribute", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx2:
            self._load_with({"drops": ["nonblock.field"]},
                            rt="zpa_policy_access_rule")
        self.assertIn("not a nested block", str(ctx2.exception))

    def test_real_overrides_all_load(self):
        from tools.registry import generated_types
        from tools.transform import load_override
        for rt in generated_types():
            load_override(rt)



class DropsCheckGateTest(unittest.TestCase):
    """DROPS_CHECK=1 turns new API surface (unacknowledged drops) into a
    red run — the tripwire the signingCertId incident needed."""

    def _run_main(self, raw, env_flag, resource_type="zpa_segment_group"):
        import io, sys, tempfile, json as _json, shutil
        from tools.transform import main
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "in.json")
            with open(src, "w", encoding="utf-8") as f:
                _json.dump(raw, f)
            if env_flag:
                os.environ["DROPS_CHECK"] = "1"
            old_err, sys.stderr = sys.stderr, io.StringIO()
            try:
                code = main([resource_type, src, "tmpdropschk"])
                err = sys.stderr.getvalue()
            finally:
                sys.stderr = old_err
                os.environ.pop("DROPS_CHECK", None)
                shutil.rmtree(os.path.join("config", "tmpdropschk"),
                              ignore_errors=True)
                shutil.rmtree(os.path.join("imports", "tmpdropschk"),
                              ignore_errors=True)
            return code, err

    RAW = [{"id": "1", "name": "G", "brandNewApiField": "x"}]

    def test_new_surface_exits_4_under_drops_check(self):
        code, err = self._run_main(self.RAW, env_flag=True)
        self.assertEqual(code, 4)
        self.assertIn("NEW API surface", err)
        self.assertIn("make triage", err)
        self.assertIn("signingCertId", err)
        self.assertIn('"acknowledged_drops"', err)
        self.assertIn('"brand_new_api_field"', err)

    def test_advisory_without_flag_but_loud(self):
        code, err = self._run_main(self.RAW, env_flag=False)
        self.assertEqual(code, 0)
        self.assertIn("NEW API surface", err)

    def test_clean_input_is_quiet_under_drops_check(self):
        code, err = self._run_main([{"id": "1", "name": "G"}],
                                   env_flag=True)
        self.assertEqual(code, 0)
        self.assertNotIn("NEW API surface", err)

    def test_known_holds_do_not_fail_drops_check(self):
        raw = [{"id": 1, "name": "Role", "aiPromptAccess": "enabled"}]
        code, err = self._run_main(
            raw, env_flag=True, resource_type="zia_admin_roles")
        self.assertEqual(code, 0)
        self.assertIn("known-held zia_admin_roles.ai_prompt_access", err)
        self.assertNotIn("NEW API surface", err)

    def test_gre_vip_read_only_extras_are_known_holds(self):
        raw = [{
            "id": "gre-1",
            "sourceIp": "192.0.2.10",
            "primaryDestVip": [{
                "id": "vip-1",
                "datacenter": "A",
                "dontProvision": True,
                "privateServiceEdge": False,
            }],
            "secondaryDestVip": [{
                "id": "vip-2",
                "datacenter": "B",
                "dontProvision": False,
                "privateServiceEdge": True,
            }],
        }]
        code, err = self._run_main(
            raw, env_flag=True,
            resource_type="zia_traffic_forwarding_gre_tunnel")
        self.assertEqual(code, 0)
        self.assertIn(
            "known-held zia_traffic_forwarding_gre_tunnel."
            "primary_dest_vip[].dont_provision",
            err,
        )
        self.assertIn(
            "known-held zia_traffic_forwarding_gre_tunnel."
            "secondary_dest_vip[].private_service_edge",
            err,
        )
        self.assertNotIn("NEW API surface", err)

    def test_dlp_web_rule_provider_gap_fields_are_known_holds(self):
        raw = [{
            "id": 1,
            "name": "DLP",
            "order": 1,
            "deeplyNestedContentEnabled": True,
            "timeoutFailClosedEnabled": False,
        }]
        code, err = self._run_main(
            raw, env_flag=True, resource_type="zia_dlp_web_rules")
        self.assertEqual(code, 0)
        self.assertIn(
            "known-held zia_dlp_web_rules.deeply_nested_content_enabled",
            err,
        )
        self.assertIn(
            "known-held zia_dlp_web_rules.timeout_fail_closed_enabled",
            err,
        )
        self.assertNotIn("NEW API surface", err)

    def test_known_holds_do_not_hide_new_surface(self):
        raw = [{
            "id": 1,
            "name": "Role",
            "aiPromptAccess": "enabled",
            "brandNewApiField": "x",
        }]
        code, err = self._run_main(
            raw, env_flag=True, resource_type="zia_admin_roles")
        self.assertEqual(code, 4)
        self.assertIn("known-held zia_admin_roles.ai_prompt_access", err)
        self.assertIn("dropped zia_admin_roles.brand_new_api_field", err)
        self.assertIn("NEW API surface", err)
        self.assertIn('"brand_new_api_field"', err)



class ServerGroupDropTriageTest(unittest.TestCase):
    def test_decorated_pull_reports_no_unacknowledged_drops(self):
        # Field report: 29 unacknowledged drops on server groups — all
        # triaged as read decoration (expandServerGroup builds the write
        # payload from schema fields only; nested expands send IDs only,
        # v4.4.4 source). The acknowledged set is the union of the SDK
        # struct fields and what real pulls showed on the same nested
        # ACG objects during policy-rule adoption.
        from tools.transform import load_override, transform_items

        raw = [{
            "id": "sg1", "name": "SG", "creationTime": "1", "modifiedBy": "u",
            "modifiedTime": "2", "readOnly": False, "restrictionType": "NONE",
            "zscalerManaged": False, "microtenantName": "Default",
            "appConnectorGroups": [{
                "id": "acg1", "name": "ACG", "cityCountry": "x, y",
                "countryCode": "US", "creationTime": "1", "enabled": True,
                "dnsQueryType": "IPV4_IPV6", "geoLocationId": "9",
                "latitude": "1", "longitude": "2", "location": "z",
                "lssAppConnectorGroup": False, "praEnabled": False,
                "selectedUpgradePriority": "WEEK",
                "upgradeDay": "SUNDAY", "upgradeTimeInSecs": "66600",
                "versionProfileId": "0", "wafDisabled": False,
            }],
            "applications": [{"id": "app1", "name": "App One",
                              "enabled": True}],
            "servers": [{"id": "srv1", "name": "S", "address": "10.0.0.1",
                         "enabled": True, "configSpace": "DEFAULT"}],
        }]
        ov = load_override("zpa_server_group")
        items, _, reported = transform_items(raw, "zpa_server_group", ov)
        self.assertEqual(reported, [])
        it = items["sg"]
        # write-carried associations keep their merged id shape
        self.assertEqual(it["app_connector_groups"], [{"id": ["acg1"]}])
        self.assertEqual(it["applications"], [{"id": ["app1"]}])
        self.assertEqual(it["servers"], [{"id": ["srv1"]}])



# Field-observed dropped paths per resource (estate-wide drop triage,
# 2026-06-12): every path verified as read decoration or provider-less
# surface — the provider write paths are built from schema fields only,
# so none of these can be write-relevant. Synthetic items carrying all
# of them must report ZERO unacknowledged drops.
ESTATE_DROPS = {
 "zcc_failopen_policy": ["id"],
 "zcc_forwarding_profile": ["id", "unified_tunnel[].id",
                            "unified_tunnel[].send_trusted_network_result_to_zpa"],
 "zcc_trusted_network": ["id", "guid"],
 "zcc_web_privacy": ["id", "name"],
 "zia_bandwidth_control_rule": ["access_control",
    "bandwidth_classes.is_name_l10n_tag", "bandwidth_classes.name",
    "default_rule", "id", "last_modified_by", "last_modified_time"],
 "zia_cloud_app_control_rule": ["access_control", "cbi_profile[].profile_seq",
    "departments.name", "device_groups.name", "groups.name", "id",
    "labels.name", "last_modified_by", "last_modified_time",
    "location_groups.name", "locations.is_name_l10n_tag", "locations.name",
    "predefined", "prompt_capture_enabled", "tenancy_profile_ids.name",
    "users.deleted", "users.name"],
 "zia_dlp_web_rules": ["access_control", "dlp_engines.is_name_l10n_tag",
    "dlp_engines.name", "file_type_categories.name",
    "file_type_categories.parent", "id", "last_modified_by",
    "last_modified_time", "location_groups.name",
    "notification_template[].name", "source_ip_groups.name",
    "url_categories.is_name_l10n_tag", "url_categories.name", "users.name"],
 "zia_location_management": ["bc_location", "cc_location", "child_count",
    "dynamiclocation_groups", "ec_location", "geo_override", "id",
    "jwt_auth", "language", "latitude", "longitude", "match_in_child",
    "non_editable", "override_dn_bandwidth", "override_up_bandwidth",
    "shared_down_bandwidth", "shared_up_bandwidth",
    "static_location_groups.name", "unused_dn_bandwidth",
    "unused_up_bandwidth"],
 "zia_rule_labels": ["created_by", "id", "last_modified_by",
                     "last_modified_time", "referenced_rule_count"],
 "zia_ssl_inspection_rules": ["access_control", "action[].id",
    "action[].ssl_interception_cert[].name", "default_rule",
    "device_groups.name", "end_point_application_groups",
    "end_point_applications", "groups.name", "id", "labels.name",
    "last_modified_by", "last_modified_time", "location_groups.name",
    "locations.is_name_l10n_tag", "locations.name", "predefined",
    "source_ip_groups.name", "users.deleted", "users.name"],
 "zia_url_categories": ["category_group", "id", "val"],
 "zia_url_filtering_rules": ["access_control", "capture_pcap",
    "cbi_profile_id", "device_groups.name", "exclude_src_countries",
    "groups.name", "groups_and_departments_set", "http_header_action_profiles",
    "http_header_profiles", "id", "labels.name", "last_modified_by",
    "last_modified_time", "location_groups.name",
    "locations.is_name_l10n_tag", "locations.name", "predefined",
    "source_ip_groups.name", "users.name", "users_and_groups_set"],
 "zpa_app_connector_group": ["connector_group_type", "connectors",
    "creation_time", "id", "microtenant_name", "modified_by",
    "modified_time", "read_only", "selected_upgrade_priority",
    "upgrade_priorities", "upgrade_priority", "version",
    "version_profile_visibility_scope", "zscaler_managed"],
 "zpa_application_segment": ["adp_enabled", "auto_app_protect_enabled",
    "creation_time", "extranet_enabled", "hbr_enabled", "id",
    "microtenant_name", "modified_by", "modified_time", "read_only",
    "server_groups[].config_space", "server_groups[].creation_time",
    "server_groups[].description", "server_groups[].dynamic_discovery",
    "server_groups[].enabled", "server_groups[].extranet_enabled",
    "server_groups[].modified_by", "server_groups[].modified_time",
    "server_groups[].name", "sticky_entity", "sticky_group",
    "zscaler_managed"],
 "zpa_microtenant_controller": ["id", "operator"],
 "zpa_policy_access_rule": ["conditions[].operands[].referenced_object_deleted",
    "app_server_groups[].config_space", "app_server_groups[].creation_time",
    "app_server_groups[].description", "app_server_groups[].dynamic_discovery",
    "app_server_groups[].enabled", "app_server_groups[].extranet_enabled",
    "app_server_groups[].modified_by", "app_server_groups[].modified_time",
    "app_server_groups[].name"],
}

ESTATE_BASE = {
 "zcc_failopen_policy": {"id": 9},
 "zcc_forwarding_profile": {"id": 9, "name": "P"},
 "zcc_trusted_network": {"id": 9, "name": "T"},
 "zcc_web_privacy": {"id": 9, "name": "W"},
 "zia_bandwidth_control_rule": {"id": 1, "name": "BW", "order": 1,
    "protocols": ["ANY_RULE"]},
 "zia_cloud_app_control_rule": {"id": 1, "name": "R",
    "type": "STREAMING_MEDIA", "order": 1, "predefined": False},
 "zia_dlp_web_rules": {"id": 1, "name": "DLP", "order": 1},
 "zia_location_management": {"id": 1, "name": "L"},
 "zia_rule_labels": {"id": 1, "name": "Lab"},
 "zia_ssl_inspection_rules": {"id": 1, "name": "R", "order": 1,
    "predefined": False, "default_rule": False},
 "zia_url_categories": {"id": "CUSTOM_01", "configured_name": "C",
    "super_category": "USER_DEFINED"},
 "zia_url_filtering_rules": {"id": 1, "name": "R", "order": 1,
    "protocols": ["ANY_RULE"], "predefined": False},
 "zpa_app_connector_group": {"id": "1", "name": "ACG"},
 "zpa_application_segment": {"id": "1", "name": "S",
    "domain_names": ["a.test"]},
 "zpa_microtenant_controller": {"id": "1", "name": "MT"},
 "zpa_policy_access_rule": {"id": "1", "name": "R", "default_rule": False},
}


class EstateDropTriageTest(unittest.TestCase):
    def test_all_field_observed_drops_are_acknowledged(self):
        from tools.transform import load_override, transform_items

        for rt in sorted(ESTATE_DROPS):
            item = dict(ESTATE_BASE[rt])
            for path in ESTATE_DROPS[rt]:
                segs = path.replace("[]", "").split(".")
                cur = item
                for seg in segs[:-1]:
                    if not isinstance(cur.get(seg), list):
                        cur[seg] = [{"id": "1"}]
                    cur = cur[seg][0]
                cur.setdefault(segs[-1], "synthval")
            _, _, reported = transform_items([item], rt, load_override(rt))
            self.assertEqual(reported, [], "%s reports: %r" % (rt, reported))

    def test_dlp_uc_template_id_is_acknowledged(self):
        from tools.transform import load_override, transform_items

        raw = [{
            "id": 1,
            "name": "DLP",
            "order": 1,
            "ucTemplateId": 7,
        }]
        _, _, reported = transform_items(
            raw, "zia_dlp_web_rules", load_override("zia_dlp_web_rules"))
        self.assertEqual(reported, [])



class Ipv6Dns64PrefixRenameTest(unittest.TestCase):
    def test_api_spelling_lands_in_schema_spelling(self):
        # Found by the SDK surface sweep with ZERO tenant data: the API
        # tag ipv6Dns64Prefix snake-cases to ipv6_dns64_prefix but the
        # provider schema spells it ipv6_dns_64prefix — without the
        # rename the setting drops from config and a provider update
        # writes d.Get(...)=false, silently FLIPPING IT OFF.
        from tools.transform import load_override, transform_items

        raw = [{"id": 1, "name": "L", "ipv6Dns64Prefix": True}]
        ov = load_override("zia_location_management")
        items, _, reported = transform_items(
            raw, "zia_location_management", ov)
        self.assertIs(items["l"]["ipv6_dns_64prefix"], True)
        self.assertEqual(reported, [])



class UnescapeOptOutTest(unittest.TestCase):
    def test_acg_descriptions_stay_escaped(self):
        # zpa_app_connector_group's provider Read uses GetAll (a paginated
        # wrapper), and the SDK's unescapeHTML is a NO-OP on wrappers — so
        # STATE keeps the escaped bytes and config must too. Unescaping
        # here created the perpetual "---->" vs "----&gt;" diff (field-hit
        # under legacy keys; the call path is auth-independent).
        from tools.transform import load_override, transform_items

        raw = [{"id": "1", "name": "ACG &amp; Edge",
                "description": "old ----&gt; new"}]
        ov = load_override("zpa_app_connector_group")
        items, _, _ = transform_items(raw, "zpa_app_connector_group", ov)
        acg = items["acg_amp_edge"]
        self.assertEqual(acg["description"], "old ----&gt; new")
        self.assertEqual(acg["name"], "ACG &amp; Edge")

    def test_forwarding_profile_stays_escaped(self):
        # zcc forwarding profile reads return a SLICE — same SDK no-op
        from tools.transform import _unescape_html_fields, load_override

        item = {"name": "P &gt; Q"}
        _unescape_html_fields(item, "zcc_forwarding_profile",
                              load_override("zcc_forwarding_profile"))
        self.assertEqual(item["name"], "P &gt; Q")

    def test_single_get_resources_still_unescape(self):
        # the other five zpa resources read via single-object GET where
        # the SDK unescape DOES fire — they keep the #68 behavior
        from tools.transform import load_override, transform_items

        raw = [{"id": "s", "name": "R&amp;D &gt; Segment",
                "domainNames": ["a.test"]}]
        ov = load_override("zpa_application_segment")
        items, _, _ = transform_items(raw, "zpa_application_segment", ov)
        self.assertEqual(items["r_d_segment"]["name"], "R&D > Segment")


if __name__ == "__main__":
    unittest.main()
