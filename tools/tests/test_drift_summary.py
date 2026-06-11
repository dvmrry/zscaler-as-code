"""Tests for tools/drift_summary.py — drift PR body generation."""
import unittest

from tools.drift_summary import diff_items, render_summary


class DiffItemsTest(unittest.TestCase):
    OLD = {
        "keep": {"name": "keep", "order": 1},
        "edit": {"name": "edit", "urls": ["a.com"], "order": 2},
        "gone": {"name": "gone"},
    }
    NEW = {
        "keep": {"name": "keep", "order": 1},
        "edit": {"name": "edit", "urls": ["a.com", "b.com"], "order": 2},
        "fresh": {"name": "fresh"},
    }

    def test_partitions_added_removed_changed(self):
        added, removed, changed = diff_items(self.OLD, self.NEW)
        self.assertEqual(added, ["fresh"])
        self.assertEqual(removed, ["gone"])
        self.assertEqual(changed, {"edit": ["urls"]})

    def test_field_added_to_item_counts_as_changed(self):
        added, removed, changed = diff_items(
            {"k": {"name": "x"}}, {"k": {"name": "x", "order": 5}})
        self.assertEqual(changed, {"k": ["order"]})

    def test_identical_is_empty(self):
        self.assertEqual(diff_items(self.OLD, self.OLD), ([], [], {}))


class RenderSummaryTest(unittest.TestCase):
    def test_no_drift_message(self):
        out = render_summary("t1", {"zia_rule_labels": ([], [], {})})
        self.assertIn("No config-level drift", out)

    def test_full_report_shape(self):
        out = render_summary("t1", {
            "zia_url_categories": (
                ["new_cat"], ["old_cat"], {"edited_cat": ["urls", "keywords"]}),
        })
        self.assertIn("### zia_url_categories", out)
        self.assertIn("+ new_cat", out)
        self.assertIn("import block staged", out)
        self.assertIn("− old_cat", out)
        self.assertIn("~ edited_cat", out)
        self.assertIn("urls, keywords", out)
        self.assertIn("0 to add/change/destroy", out)

    def test_untouched_resources_omitted(self):
        out = render_summary("t1", {
            "zia_rule_labels": ([], [], {}),
            "zia_url_categories": (["x"], [], {}),
        })
        self.assertNotIn("zia_rule_labels", out)


if __name__ == "__main__":
    unittest.main()
