"""Tests for the zero-write state fill (tools/statefill.py).

State surgery gets the strictest pins in the suite: every guard has a
refusal test, the produced shape is asserted byte-precisely against the
provider schema, and nothing outside the one instance may change.
"""
import json
import unittest

from tools.statefill import build_state_value, fill_state

RT = "zia_url_filtering_rules"
KEY = "isolate_rule"

CONFIG_ITEMS = {
    KEY: {
        "name": "Isolate Rule", "order": 5, "action": "ISOLATE",
        "cbi_profile": [{
            "id": "prof-1", "name": "Isolation Profile",
            "profile_seq": "27203929", "url": "https://x.test/p",
        }],
    },
}


def _state(attrs_extra=None, serial=7):
    attrs = {"id": "201695", "name": "Isolate Rule", "order": 5}
    attrs.update(attrs_extra or {})
    return {
        "version": 4,
        "serial": serial,
        "lineage": "abc-123",
        "resources": [
            {"module": "module.%s" % RT, "mode": "managed", "type": RT,
             "name": "this",
             "instances": [{"index_key": KEY, "attributes": attrs}]},
            {"module": "module.other", "mode": "managed", "type": "other",
             "name": "this",
             "instances": [{"index_key": "x", "attributes": {"id": "1"}}]},
        ],
    }


class FillStateTest(unittest.TestCase):
    def test_fills_schema_complete_shape_and_bumps_serial(self):
        doc, summary = fill_state(_state(), RT, KEY, "cbi_profile",
                                  CONFIG_ITEMS)
        inst = doc["resources"][0]["instances"][0]
        # every inner schema attr present; profile_seq coerced to number
        self.assertEqual(inst["attributes"]["cbi_profile"], [{
            "id": "prof-1", "name": "Isolation Profile",
            "profile_seq": 27203929, "url": "https://x.test/p",
        }])
        self.assertEqual(doc["serial"], 8)
        self.assertEqual(doc["lineage"], "abc-123")
        self.assertTrue(any("serial 7 -> 8" in l for l in summary))

    def test_everything_else_untouched(self):
        original = _state()
        doc, _ = fill_state(original, RT, KEY, "cbi_profile", CONFIG_ITEMS)
        # input not mutated
        self.assertNotIn("cbi_profile", original["resources"][0]
                         ["instances"][0]["attributes"])
        # the unrelated resource is byte-identical
        self.assertEqual(doc["resources"][1], original["resources"][1])

    def test_refuses_to_overwrite_non_empty_state(self):
        state = _state(attrs_extra={"cbi_profile": [{"id": "other"}]})
        with self.assertRaises(ValueError) as ctx:
            fill_state(state, RT, KEY, "cbi_profile", CONFIG_ITEMS)
        self.assertIn("refusing to overwrite", str(ctx.exception))

    def test_empty_list_in_state_is_fillable(self):
        state = _state(attrs_extra={"cbi_profile": []})
        doc, _ = fill_state(state, RT, KEY, "cbi_profile", CONFIG_ITEMS)
        self.assertEqual(
            doc["resources"][0]["instances"][0]["attributes"]
            ["cbi_profile"][0]["id"], "prof-1")

    def test_refuses_when_config_lacks_field(self):
        items = {KEY: {"name": "Isolate Rule"}}
        with self.assertRaises(ValueError) as ctx:
            fill_state(_state(), RT, KEY, "cbi_profile", items)
        self.assertIn("does not carry", str(ctx.exception))

    def test_refuses_when_instance_not_imported(self):
        with self.assertRaises(ValueError) as ctx:
            fill_state(_state(), RT, "ghost", "cbi_profile",
                       {"ghost": CONFIG_ITEMS[KEY]})
        self.assertIn("IMPORTED first", str(ctx.exception))

    def test_refuses_unknown_field(self):
        with self.assertRaises(ValueError) as ctx:
            fill_state(_state(), RT, KEY, "not_a_field",
                       {KEY: {"not_a_field": "x"}})
        self.assertIn("not a field", str(ctx.exception))

    def test_refuses_config_keys_outside_schema(self):
        items = {KEY: {"cbi_profile": [{"id": "p", "rogue": "x"}]}}
        with self.assertRaises(ValueError) as ctx:
            fill_state(_state(), RT, KEY, "cbi_profile", items)
        self.assertIn("schema lacks", str(ctx.exception))


class BuildStateValueTest(unittest.TestCase):
    def test_missing_inner_attrs_become_null(self):
        from tools.tfschema import load_resource
        fs = load_resource(RT)["block"]["block_types"]["cbi_profile"]
        out = build_state_value(fs, [{"id": "p"}])
        self.assertEqual(out, [{"id": "p", "name": None,
                                "profile_seq": None, "url": None}])



class RemainingGuardTest(unittest.TestCase):
    def test_refuses_computed_only_fields(self):
        # rule_id is computed-only on url_filtering — the provider owns
        # it; filling it would be invention, not correction
        with self.assertRaises(ValueError) as ctx:
            fill_state(_state(), RT, KEY, "rule_id",
                       {KEY: {"rule_id": 99}})
        self.assertIn("computed-only", str(ctx.exception))

    def test_refuses_when_key_missing_from_config_entirely(self):
        with self.assertRaises(ValueError) as ctx:
            fill_state(_state(), RT, KEY, "cbi_profile", {})
        self.assertIn("no item", str(ctx.exception))

    def test_empty_string_config_value_is_not_a_source(self):
        with self.assertRaises(ValueError) as ctx:
            fill_state(_state(), RT, KEY, "block_override",
                       {KEY: {"block_override": ""}})
        self.assertIn("does not carry", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
