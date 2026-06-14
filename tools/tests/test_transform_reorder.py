"""Tests for the derived zpa_policy_access_rule_reorder config.

The reorder resource has no fetch/import; its config is built from the access
rules' (still-returned, deprecated) order. derive_reorder turns a raw
access-rule pull into one reorder item per policy_type. Stdlib-only, offline.
"""
import unittest

from tools.transform import derive_reorder

DERIVE = {"from": "zpa_policy_access_rule", "policy_type": "ACCESS_POLICY"}


class DeriveReorderTest(unittest.TestCase):
    def test_maps_id_and_ruleorder_keyed_by_policy_type(self):
        src = [{"id": "10", "ruleOrder": "2"}, {"id": "20", "ruleOrder": "1"}]
        out = derive_reorder(src, DERIVE)
        self.assertEqual(list(out), ["ACCESS_POLICY"])
        entry = out["ACCESS_POLICY"]
        self.assertEqual(entry["policy_type"], "ACCESS_POLICY")
        # sorted by order numerically: rule 20 (order 1) before rule 10 (order 2)
        self.assertEqual(entry["rules"],
                         [{"id": "20", "order": "1"}, {"id": "10", "order": "2"}])

    def test_no_resource_id_in_output(self):
        out = derive_reorder([{"id": "1", "ruleOrder": "1"}], DERIVE)
        self.assertNotIn("id", out["ACCESS_POLICY"])

    def test_numeric_order_sorts_naturally_not_lexically(self):
        src = [{"id": "a", "ruleOrder": "10"}, {"id": "b", "ruleOrder": "9"}]
        out = derive_reorder(src, DERIVE)
        self.assertEqual([r["order"] for r in out["ACCESS_POLICY"]["rules"]],
                         ["9", "10"])

    def test_raises_on_rule_missing_id_or_order(self):
        # the create is only safe if the list is COMPLETE; a partial reorder
        # would silently re-rank the omitted rules, so refuse it loudly.
        with self.assertRaises(ValueError):
            derive_reorder([{"id": "1", "ruleOrder": "1"}, {"id": "2"}], DERIVE)
        with self.assertRaises(ValueError):
            derive_reorder([{"ruleOrder": "3"}], DERIVE)

    def test_empty_source_yields_empty(self):
        self.assertEqual(derive_reorder([], DERIVE), {})

    def test_deterministic(self):
        src = [{"id": "10", "ruleOrder": "1"}, {"id": "10", "ruleOrder": "1"}]
        self.assertEqual(derive_reorder(src, DERIVE), derive_reorder(src, DERIVE))


if __name__ == "__main__":
    unittest.main()
