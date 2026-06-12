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
        # the merged-but-never-applied tell, pre-interpreted for the reviewer
        self.assertIn("merged but never APPLIED", out)

    def test_mass_change_banner_on_many_items(self):
        changed = {"r%d" % i: ["description"] for i in range(12)}
        out = render_summary("t1", {"zia_url_filtering_rules": ([], [], changed)})
        self.assertIn("MASS CHANGE", out)
        self.assertIn("12 item(s)", out)

    def test_same_field_cascade_banner(self):
        # the ZIA order-cascade shape: one field across many items
        changed = {"r%d" % i: ["order"] for i in range(8)}
        out = render_summary("t1", {"zia_url_filtering_rules": ([], [], changed)})
        self.assertIn("`order` changed on 8 item(s)", out)
        self.assertIn("re-shuffle", out)

    def test_small_drift_has_no_banner(self):
        out = render_summary("t1", {
            "zia_url_categories": (["x"], [], {"y": ["urls"]})})
        self.assertNotIn("MASS CHANGE", out)
        self.assertNotIn(":warning:", out)

    def test_untouched_resources_omitted(self):
        out = render_summary("t1", {
            "zia_rule_labels": ([], [], {}),
            "zia_url_categories": (["x"], [], {}),
        })
        self.assertNotIn("zia_rule_labels", out)


if __name__ == "__main__":
    unittest.main()
