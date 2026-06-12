"""Tests for the plan-diff policy gates (tools/plan_checks.py).

Both checks judge NEW additions only — pins cover the field-specified
behaviors verbatim: wildcard coverage fails redundancy (except inside
the exemptions category), and a new host under an exemptions suffix
must be an exact exemptions entry.
"""
import unittest

from tools.plan_checks import (
    added_hosts, check_redundancy, check_ssl_bypass,
)

EXEMPT = "ssl_exemptions"

CATEGORIES = {
    EXEMPT: {"urls": [".example.com", "exact.example.com"]},
    "blocklist": {"urls": ["*.corp.test", ".old.test", "plain.test"]},
    "alloweds": {"urls": ["other.test"]},
}


def _plan(before, after, key="blocklist", action="update"):
    return {"format_version": "1.2", "resource_changes": [{
        "type": "zia_url_categories", "index": key,
        "change": {"actions": [action],
                   "before": before, "after": after},
    }]}


class AddedHostsTest(unittest.TestCase):
    def test_update_diff_and_create_full(self):
        plan = _plan({"urls": ["a.test"]}, {"urls": ["a.test", "b.test"]})
        self.assertEqual(added_hosts(plan), [("blocklist", "b.test")])
        plan = _plan(None, {"urls": ["x.test"]}, action="create")
        self.assertEqual(added_hosts(plan), [("blocklist", "x.test")])

    def test_unchanged_and_other_resources_ignored(self):
        plan = {"format_version": "1.2", "resource_changes": [
            {"type": "zia_url_filtering_rules", "index": "r",
             "change": {"actions": ["update"], "before": {},
                        "after": {"urls": ["x.test"]}}},
            {"type": "zia_url_categories", "index": "c",
             "change": {"actions": ["no-op"], "before": {}, "after": {}}},
        ]}
        self.assertEqual(added_hosts(plan), [])


class RedundancyTest(unittest.TestCase):
    def test_concrete_subdomain_under_wildcard_fails(self):
        # the spec's example: *.corp.test exists, adding www.corp.test fails
        fails = check_redundancy([("alloweds", "www.corp.test")],
                                 CATEGORIES, EXEMPT)
        self.assertEqual(len(fails), 1)
        self.assertIn("www.corp.test", fails[0])
        self.assertIn("*.corp.test", fails[0])

    def test_leading_dot_base_also_covers(self):
        fails = check_redundancy([("alloweds", "deep.sub.old.test")],
                                 CATEGORIES, EXEMPT)
        self.assertEqual(len(fails), 1)

    def test_exemptions_category_is_exempt(self):
        # adding concrete hosts to SSL exemptions is what check 2 REQUIRES
        fails = check_redundancy([(EXEMPT, "www.example.com")],
                                 CATEGORIES, EXEMPT)
        self.assertEqual(fails, [])

    def test_uncovered_host_passes(self):
        fails = check_redundancy([("alloweds", "fresh.other.test")],
                                 CATEGORIES, EXEMPT)
        self.assertEqual(fails, [])

    def test_adding_a_base_itself_is_not_redundant(self):
        fails = check_redundancy([("alloweds", ".newbase.test")],
                                 CATEGORIES, EXEMPT)
        self.assertEqual(fails, [])

    def test_concrete_entry_does_not_cover(self):
        # plain.test is concrete, not a base — sub.plain.test is fine
        fails = check_redundancy([("alloweds", "sub.plain.test")],
                                 CATEGORIES, EXEMPT)
        self.assertEqual(fails, [])


class SslBypassTest(unittest.TestCase):
    def test_host_under_suffix_not_exact_fails(self):
        # the spec's example: exemptions has .example.com, adding
        # www.example.com to a block category fails
        fails = check_ssl_bypass([("blocklist", "www.example.com")],
                                 CATEGORIES, EXEMPT)
        self.assertEqual(len(fails), 1)
        self.assertIn("www.example.com", fails[0])
        self.assertIn(".example.com", fails[0])

    def test_host_with_exact_exemption_entry_passes(self):
        fails = check_ssl_bypass([("blocklist", "exact.example.com")],
                                 CATEGORIES, EXEMPT)
        self.assertEqual(fails, [])

    def test_host_outside_suffixes_passes(self):
        fails = check_ssl_bypass([("blocklist", "www.unrelated.test")],
                                 CATEGORIES, EXEMPT)
        self.assertEqual(fails, [])

    def test_additions_to_exemptions_itself_pass(self):
        fails = check_ssl_bypass([(EXEMPT, "www.example.com")],
                                 CATEGORIES, EXEMPT)
        self.assertEqual(fails, [])


if __name__ == "__main__":
    unittest.main()
