"""Tests for tools/plan_summary.py — the reviewer's counts-first row."""
import unittest

from tools.plan_summary import summarize


def plan(*changes):
    return {"resource_changes": [{"change": c} for c in changes]}


class SummarizeTest(unittest.TestCase):
    def test_import_only_plan(self):
        p = plan({"actions": ["no-op"], "importing": {"id": "1"}},
                 {"actions": ["no-op"], "importing": {"id": "2"}})
        row, destroys = summarize(p, "zs2/zia_rule_labels")
        self.assertEqual(row, "| zs2/zia_rule_labels | 2 | 0 | 0 | 0 |")
        self.assertEqual(destroys, 0)

    def test_mixed_actions(self):
        p = plan({"actions": ["create"]},
                 {"actions": ["update"]},
                 {"actions": ["delete"]},
                 {"actions": ["no-op"]})
        row, destroys = summarize(p, "t/r")
        self.assertEqual(row, "| t/r | 0 | 1 | 1 | 1 |")
        self.assertEqual(destroys, 1)

    def test_replace_counts_as_add_and_destroy(self):
        p = plan({"actions": ["delete", "create"]})
        row, destroys = summarize(p, "t/r")
        self.assertEqual(row, "| t/r | 0 | 1 | 0 | 1 |")
        self.assertEqual(destroys, 1)

    def test_empty_plan(self):
        row, destroys = summarize({}, "t/r")
        self.assertEqual(row, "| t/r | 0 | 0 | 0 | 0 |")
        self.assertEqual(destroys, 0)


if __name__ == "__main__":
    unittest.main()
