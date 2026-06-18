"""Tests for the plan-diff policy gates (tools/plan_checks.py).

Checks judge NEW additions only — pins cover the field-specified
behaviors verbatim: wildcard coverage fails redundancy (except inside
the exemptions category), a new host under an exemptions suffix must be
an exact exemptions entry, and new location IP ranges must not overlap.
"""
import unittest

from tools.plan_checks import (
    added_hosts, added_location_ranges, check_location_ip_overlap,
    check_redundancy, check_ssl_bypass,
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


def _location_plan(before, after, key="branch", action="update"):
    return {"format_version": "1.2", "resource_changes": [{
        "type": "zia_location_management", "index": key,
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


class AddedLocationRangesTest(unittest.TestCase):
    def test_update_diff_and_create_full(self):
        plan = _location_plan(
            {"ip_addresses": ["10.0.0.0/24"]},
            {"ip_addresses": ["10.0.0.0/24", "10.1.0.0/24"]})
        self.assertEqual(added_location_ranges(plan),
                         [("branch", "10.1.0.0/24")])
        plan = _location_plan(None, {"ip_addresses": ["192.0.2.1"]},
                              action="create")
        self.assertEqual(added_location_ranges(plan),
                         [("branch", "192.0.2.1")])


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


class LocationIpOverlapTest(unittest.TestCase):
    def test_new_cidr_overlapping_existing_location_fails(self):
        locations = {
            "hq": {"ip_addresses": ["10.10.0.0/16"]},
            "branch": {"ip_addresses": ["192.0.2.10-192.0.2.20"]},
        }
        fails = check_location_ip_overlap(
            [("new_branch", "10.10.20.0/24")], locations)
        self.assertEqual(len(fails), 1)
        self.assertIn("LOCATION-IP-OVERLAP", fails[0])
        self.assertIn("hq", fails[0])

    def test_new_ranges_that_do_not_overlap_pass(self):
        locations = {"hq": {"ip_addresses": ["10.10.0.0/16"]}}
        fails = check_location_ip_overlap(
            [("new_branch", "10.11.0.0/24")], locations)
        self.assertEqual(fails, [])

    def test_same_plan_overlaps_are_caught(self):
        locations = {}
        fails = check_location_ip_overlap([
            ("a", "203.0.113.1-203.0.113.10"),
            ("b", "203.0.113.5"),
        ], locations)
        self.assertEqual(len(fails), 1)
        self.assertIn("location 'a'", fails[0])

    def test_invalid_new_range_fails(self):
        fails = check_location_ip_overlap([("a", "10.0.0.9-10.0.0.1")], {})
        self.assertEqual(len(fails), 1)
        self.assertIn("not an IP / CIDR / address range", fails[0])


if __name__ == "__main__":
    unittest.main()
