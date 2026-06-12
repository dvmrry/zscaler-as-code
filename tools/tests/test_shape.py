"""Tests for the shape report (tools/shape.py).

The contract under test is a SECURITY contract: no input value may
survive into the report (it is destined for relay out of restricted
environments), while the structure must stay diagnostic — equal values
keep equal tokens, list reorders are named as reorders, changed paths
are named precisely. Several cases are regression pins from a live
adversarial leak-hunt (map-typed attribute KEYS, structural-literal
false positives, arbitrary action strings).
"""
import json
import unittest

from tools.shape import (
    Tokenizer, build_report, diff_lines, known_field_names, sanitize,
    self_check,
)

ALLOW = known_field_names()

SENSITIVE = [
    "Finance SCIM Rule", "finance.example.internal", "3251059",
    "216196257331285825", "Engineering Group", "consumer_large_file_50g",
    "X-Tenant-Org-Header-12345",
]


def _plan(resource_changes):
    return {"format_version": "1.2", "resource_changes": resource_changes}


def _update(address, before, after, rtype="zpa_policy_access_rule",
            actions=None):
    return {"address": address, "type": rtype, "name": "this",
            "change": {"actions": actions or ["update"], "before": before,
                       "after": after}}


class LeakInvariantTest(unittest.TestCase):
    """No planted sensitive string may appear in any report mode."""

    def assert_clean(self, doc, only=None):
        text, secrets, kept = build_report(doc, only=only)
        for value in SENSITIVE:
            self.assertNotIn(value, text)
        self.assertTrue(self_check(text, secrets, kept))
        return text

    def test_plan_mode_leaks_nothing(self):
        before = {
            "name": SENSITIVE[0],
            "conditions": [{"operator": "OR", "operands": [
                {"object_type": "SCIM_GROUP", "lhs": SENSITIVE[3],
                 "rhs": SENSITIVE[2], "name": SENSITIVE[4]},
            ]}],
        }
        after = json.loads(json.dumps(before))
        after["conditions"][0]["operands"][0]["name"] = SENSITIVE[1]
        doc = _plan([_update(
            'module.zpa_policy_access_rule.this["%s"]' % SENSITIVE[0],
            before, after)])
        text = self.assert_clean(doc)
        self.assertIn("plan:", text)

    def test_tfvars_mode_leaks_nothing(self):
        doc = {"items": {
            SENSITIVE[0]: {"name": SENSITIVE[0], "order": 3,
                           "urls": [SENSITIVE[1]]},
            "plain": {"name": SENSITIVE[5], "size_quota": 51200},
        }}
        text = self.assert_clean(doc)
        # field names (schema-derived) stay readable
        self.assertIn('"urls"', text)
        self.assertIn('"size_quota"', text)

    def test_raw_pull_mode_leaks_nothing(self):
        doc = [{"id": SENSITIVE[3], "name": SENSITIVE[0],
                "domainNames": [SENSITIVE[1]]}]
        self.assert_clean(doc)

    def test_importing_id_tokenized(self):
        rc = _update('module.x.this["a"]', {}, {})
        rc["change"]["actions"] = ["no-op"]
        rc2 = {"address": 'module.x.this["b"]', "type": "zpa_x",
               "name": "this",
               "change": {"actions": ["update"], "before": {}, "after": {},
                          "importing": {"id": SENSITIVE[3]}}}
        text = self.assert_clean(_plan([rc, rc2]))
        self.assertIn("importing id id1", text)

    def test_map_typed_attribute_keys_tokenized_plan(self):
        # leak-hunt blocker: tenant data can live in dict KEYS of
        # map-typed attributes — unknown keys must be tokenized in all
        # modes, including diff paths
        doc = _plan([_update(
            "module.x.this",
            {"conditions": {SENSITIVE[6]: "v-old"}},
            {"conditions": {SENSITIVE[6]: "v-new"}},
        )])
        text = self.assert_clean(doc)
        self.assertIn("conditions.k", text)

    def test_map_typed_attribute_keys_tokenized_tfvars_and_raw(self):
        self.assert_clean({"items": {"item_one": {
            "name": "x", "conditions": [{SENSITIVE[6]: "v"}]}}})
        self.assert_clean([{"name": "x",
                            "memberships": {SENSITIVE[6]: {"id": "1"}}}])

    def test_structural_literal_values_do_not_block_emission(self):
        # leak-hunt blocker (inverse direction): a VALUE equal to a JSON
        # or action literal ("true", "create") is tokenized as usual and
        # must NOT make self_check refuse a clean report
        doc = _plan([_update("module.x.this", None,
                             {"enabled": True, "name": "true"},
                             actions=["create"])])
        text, secrets, kept = build_report(doc)
        self.assertTrue(self_check(text, secrets, kept))

    def test_unknown_action_strings_not_echoed(self):
        doc = _plan([_update("module.x.this", {}, {},
                             actions=["SECRET_ACTION_VALUE"])])
        text, secrets, kept = build_report(doc)
        self.assertNotIn("SECRET_ACTION_VALUE", text)
        self.assertIn("other-action", text)
        self.assertTrue(self_check(text, secrets, kept))


class DiagnosticPowerTest(unittest.TestCase):
    """Sanitized must still mean diagnosable."""

    def test_equal_values_share_tokens(self):
        tok = Tokenizer()
        a = tok.token("3251059")
        b = tok.token("3251059")
        c = tok.token("3251058")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_int_float_same_json_number_same_token(self):
        tok = Tokenizer()
        self.assertEqual(tok.token(1), tok.token(1.0))
        self.assertNotEqual(tok.token(1), tok.token(1.5))

    def test_reorder_named_as_reorder(self):
        before = {"operands": [
            {"rhs": "A-value-1"}, {"rhs": "B-value-2"}, {"rhs": "C-value-3"},
        ]}
        after = {"operands": [
            {"rhs": "C-value-3"}, {"rhs": "A-value-1"}, {"rhs": "B-value-2"},
        ]}
        lines = []
        diff_lines(before, after, Tokenizer(), ALLOW, "", lines)
        self.assertEqual(len(lines), 1)
        self.assertIn("REORDER only", lines[0])
        self.assertIn("[2, 0, 1]", lines[0])

    def test_value_rewrite_named_per_path(self):
        before = {"conditions": [{"operands": [
            {"object_type": "APP", "name": "Old Display"},
        ]}]}
        after = {"conditions": [{"operands": [
            {"object_type": "APP", "name": "New Display"},
        ]}]}
        lines = []
        diff_lines(before, after, Tokenizer(), ALLOW, "", lines)
        self.assertEqual(len(lines), 1)
        self.assertIn("conditions[0].operands[0].name:", lines[0])
        self.assertNotIn("Old Display", lines[0])
        self.assertNotIn("New Display", lines[0])

    def test_added_and_removed_paths(self):
        lines = []
        diff_lines({"description": "gone-value"}, {"enabled": True},
                   Tokenizer(), ALLOW, "", lines)
        self.assertTrue(any(l.startswith("  - description") for l in lines))
        self.assertTrue(any(l.startswith("  + enabled") for l in lines))

    def test_plan_filter_by_resource_type(self):
        doc = _plan([
            _update('module.a.this["x"]', {"name": "one-value"},
                    {"name": "two-value"}, rtype="zpa_policy_access_rule"),
            _update('module.b.this["y"]', {"name": "one-value"},
                    {"name": "two-value"}, rtype="zia_url_filtering_rules"),
        ])
        text = build_report(doc, only="zpa_policy_access_rule")[0]
        self.assertEqual(text.count("(update)"), 1)

    def test_determinism(self):
        doc = {"items": {"k": {"name": "Some Name", "order": 2}}}
        self.assertEqual(build_report(doc)[0], build_report(doc)[0])

    def test_self_check_catches_a_leak(self):
        # the gate must actually gate: a report that embeds an input
        # value is refused
        self.assertFalse(self_check(
            "report containing 3251059 verbatim", {"3251059"}, set()))


class SanitizeShapeTest(unittest.TestCase):
    def test_item_keys_tokenized_field_keys_kept(self):
        doc = {"items": {"secret_key_name": {"name": "secret_key_name"}}}
        out = sanitize(doc, Tokenizer(), ALLOW, key_depth=1)
        self.assertEqual(list(out), ["items"])
        (item_key,) = out["items"]
        self.assertTrue(item_key.startswith("k"))
        self.assertEqual(list(out["items"][item_key]), ["name"])

    def test_item_key_equal_to_schema_word_still_tokenized(self):
        # depth-0 keys are ALWAYS data, even when they collide with a
        # schema field name like "name"
        out = sanitize({"items": {"name": {"order": 1}}},
                       Tokenizer(), ALLOW, key_depth=1)
        (item_key,) = out["items"]
        self.assertTrue(item_key.startswith("k"))

    def test_camel_case_pull_keys_kept_when_schema_known(self):
        out = sanitize({"domainNames": ["v"], "creationTime": "1"},
                       Tokenizer(), ALLOW)
        self.assertIn("domainNames", out)
        self.assertIn("creationTime", out)

    def test_bool_and_null_stay_literal(self):
        out = sanitize({"enabled": True, "description": None, "order": 7},
                       Tokenizer(), ALLOW)
        self.assertEqual(out["enabled"], True)
        self.assertIsNone(out["description"])
        self.assertEqual(out["order"], "n1")


if __name__ == "__main__":
    unittest.main()
