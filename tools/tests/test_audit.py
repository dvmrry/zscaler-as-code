"""Tests for tools/audit.py — offline parts only (parse/filter/render).

The network flow (async report request/poll/download) is exercised at
work like the fetcher was; everything that consumes its output is
testable here, including degraded shapes.
"""
import unittest

from tools.audit import filter_rows, parse_audit_rows, render_attribution

CSV = """\
Time,Action,Category,Sub Category,Resource,Interface,Admin ID,Client IP,Result
"Jun 11, 2026 9:14:02 AM EDT",UPDATE,URL_CATEGORIES,,"ssl_bypass_list",UI,infosec.admin@example.invalid,10.0.0.5,SUCCESS
"Jun 11, 2026 9:20:44 AM EDT",SIGN_IN,AUTHENTICATION,,,UI,someone.else@example.invalid,10.0.0.9,SUCCESS
"""


class ParseTest(unittest.TestCase):
    def test_parses_rows_with_fuzzy_columns(self):
        rows = parse_audit_rows(CSV)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["action"], "UPDATE")
        self.assertEqual(rows[0]["admin"], "infosec.admin@example.invalid")
        self.assertIn("URL_CATEGOR", rows[0]["category"])

    def test_garbage_input_yields_no_rows(self):
        self.assertEqual(parse_audit_rows("not,a,real\nreport"), [])
        self.assertEqual(parse_audit_rows(""), [])


class FilterTest(unittest.TestCase):
    def test_splits_matched_vs_other(self):
        rows = parse_audit_rows(CSV)
        matched, other = filter_rows(rows, ["zia_url_categories"])
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["admin"], "infosec.admin@example.invalid")
        self.assertEqual(len(other), 1)

    def test_unknown_resource_type_matches_nothing(self):
        rows = parse_audit_rows(CSV)
        matched, other = filter_rows(rows, ["zpa_segment_group"])
        self.assertEqual(matched, [])
        self.assertEqual(len(other), 2)


class RenderTest(unittest.TestCase):
    def test_renders_table_and_collapsible_other(self):
        rows = parse_audit_rows(CSV)
        matched, other = filter_rows(rows, ["zia_url_categories"])
        out = render_attribution(matched, other, 24)
        self.assertIn("| Time | Admin |", out)
        self.assertIn("infosec.admin@example.invalid", out)
        self.assertIn("<details>", out)

    def test_no_matches_message(self):
        out = render_attribution([], [], 24)
        self.assertIn("No audit entries matched", out)


if __name__ == "__main__":
    unittest.main()
