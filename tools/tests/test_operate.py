"""Tests for tools/operate.py — the BAU config-edit primitives.

The contract these pin: only allowlisted list-of-strings fields are touched,
edits are idempotent and canonically sorted, the file stays byte-identical to a
fresh transform (render_tfvars), and a wrong key/field is refused loudly (so the
operate-change workflow can't silently corrupt config).
"""
import json
import os
import shutil
import tempfile
import unittest

from tools import operate
from tools.transform import render_tfvars


def _write(root, tenant, resource_type, items):
    d = os.path.join(root, tenant)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, resource_type + operate.CONFIG_SUFFIX)
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_tfvars(items))
    return path


class OperateTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        _write(self.root, "t", "zia_url_categories", {
            "blocked_social": {"configured_name": "Blocked Social",
                               "urls": ["reddit.com"],
                               "keywords": ["malware"],
                               "ip_ranges": ["10.1.0.0/24"]},
        })
        _write(self.root, "t", "zpa_application_segment", {
            "redhat_seg": {"name": "Red Hat", "domain_names": ["rh.example.test"]},
        })
        _write(self.root, "t", "zia_location_management", {
            "hq_office": {"name": "HQ Office", "ip_addresses": ["10.0.0.0/24"]},
        })

    def _path(self, resource_type):
        return os.path.join(self.root, "t", resource_type + operate.CONFIG_SUFFIX)

    def _read(self, resource_type):
        with open(self._path(resource_type), encoding="utf-8") as f:
            return f.read()

    def _items(self, resource_type):
        return json.loads(self._read(resource_type))["items"]

    def test_add_url_appends_and_sorts(self):
        msg = operate.operate("add", "t", "zia_url_categories", "blocked_social",
                              "urls", "facebook.com", config_root=self.root)
        self.assertIn("add facebook.com", msg)
        self.assertEqual(self._items("zia_url_categories")["blocked_social"]["urls"],
                         ["facebook.com", "reddit.com"])  # canonical sort

    def test_add_existing_is_noop(self):
        before = self._read("zia_url_categories")
        msg = operate.operate("add", "t", "zia_url_categories", "blocked_social",
                              "urls", "reddit.com", config_root=self.root)
        self.assertTrue(msg.startswith("no-op"))
        self.assertEqual(before, self._read("zia_url_categories"))  # no write on no-op

    def test_remove_present(self):
        msg = operate.operate("remove", "t", "zia_url_categories", "blocked_social",
                              "urls", "reddit.com", config_root=self.root)
        self.assertIn("remove reddit.com", msg)
        self.assertEqual(self._items("zia_url_categories")["blocked_social"]["urls"], [])

    def test_remove_absent_is_noop(self):
        msg = operate.operate("remove", "t", "zia_url_categories", "blocked_social",
                              "urls", "nope.com", config_root=self.root)
        self.assertTrue(msg.startswith("no-op"))

    def test_add_domain_to_segment(self):
        operate.operate("add", "t", "zpa_application_segment", "redhat_seg",
                        "domain_names", "access.example.test", config_root=self.root)
        self.assertEqual(
            self._items("zpa_application_segment")["redhat_seg"]["domain_names"],
            ["access.example.test", "rh.example.test"])

    def test_unsafe_field_refused(self):
        # tcp_port_ranges is structured (paired) — must never be edited here.
        with self.assertRaises(ValueError):
            operate.operate("add", "t", "zpa_application_segment", "redhat_seg",
                            "tcp_port_ranges", "443", config_root=self.root)

    def test_keyword_field_now_editable(self):
        # keywords is now allowlisted (free text, no validator) -- the add succeeds.
        msg = operate.operate("add", "t", "zia_url_categories", "blocked_social",
                              "keywords", "phish", config_root=self.root)
        self.assertIn("add phish", msg)
        self.assertEqual(
            self._items("zia_url_categories")["blocked_social"]["keywords"],
            ["malware", "phish"])

    def test_add_keyword_appends_and_sorts(self):
        operate.operate("add", "t", "zia_url_categories", "blocked_social",
                        "keywords", "phish", config_root=self.root)
        self.assertEqual(
            self._items("zia_url_categories")["blocked_social"]["keywords"],
            ["malware", "phish"])

    def test_add_ip_range_valid(self):
        operate.operate("add", "t", "zia_url_categories", "blocked_social",
                        "ip_ranges", "10.2.0.0/24", config_root=self.root)
        self.assertEqual(
            self._items("zia_url_categories")["blocked_social"]["ip_ranges"],
            ["10.1.0.0/24", "10.2.0.0/24"])

    def test_add_ip_range_dash_range_valid(self):
        operate.operate("add", "t", "zia_url_categories", "blocked_social",
                        "ip_ranges", "10.0.0.1-10.0.0.9", config_root=self.root)
        self.assertIn(
            "10.0.0.1-10.0.0.9",
            self._items("zia_url_categories")["blocked_social"]["ip_ranges"])

    def test_add_ip_range_invalid_refused(self):
        for bad in ("10.0.0.300", "10.0.0.0/33"):
            before = self._read("zia_url_categories")
            with self.assertRaises(ValueError):
                operate.operate("add", "t", "zia_url_categories", "blocked_social",
                                "ip_ranges", bad, config_root=self.root)
            self.assertEqual(before, self._read("zia_url_categories"))

    def test_remove_invalid_ip_still_works(self):
        _write(self.root, "t", "zia_url_categories", {
            "blocked_social": {"configured_name": "Blocked Social",
                               "urls": ["reddit.com"],
                               "keywords": ["malware"],
                               "ip_ranges": ["10.1.0.0/24", "10.0.0.300"]},
        })
        msg = operate.operate("remove", "t", "zia_url_categories", "blocked_social",
                              "ip_ranges", "10.0.0.300", config_root=self.root)
        self.assertIn("remove 10.0.0.300", msg)
        self.assertEqual(
            self._items("zia_url_categories")["blocked_social"]["ip_ranges"],
            ["10.1.0.0/24"])

    def test_add_location_ip(self):
        operate.operate("add", "t", "zia_location_management", "hq_office",
                        "ip_addresses", "10.5.0.0/24", config_root=self.root)
        self.assertEqual(
            self._items("zia_location_management")["hq_office"]["ip_addresses"],
            ["10.0.0.0/24", "10.5.0.0/24"])

    def test_add_location_ip_invalid_refused(self):
        with self.assertRaises(ValueError):
            operate.operate("add", "t", "zia_location_management", "hq_office",
                            "ip_addresses", "10.0.0.300", config_root=self.root)

    def test_invalid_tenant_refused(self):
        for bad in ("../etc", "..", ".", "a/b"):
            with self.assertRaises(ValueError):
                operate.operate("add", bad, "zia_url_categories", "blocked_social",
                                "urls", "x.com", config_root=self.root)

    def test_unknown_key_refused_with_candidates(self):
        with self.assertRaises(ValueError) as cm:
            operate.operate("add", "t", "zia_url_categories", "Blocked Social",
                            "urls", "x.com", config_root=self.root)
        self.assertIn("blocked_social", str(cm.exception))  # lists the real key

    def test_missing_config_refused(self):
        with self.assertRaises(ValueError):
            operate.operate("add", "t", "zia_url_categories", "k", "urls", "x.com",
                            config_root=os.path.join(self.root, "nope"))

    def test_write_is_canonical(self):
        operate.operate("add", "t", "zia_url_categories", "blocked_social",
                        "urls", "a.com", config_root=self.root)
        text = self._read("zia_url_categories")
        self.assertEqual(text, render_tfvars(json.loads(text)["items"]))
        self.assertTrue(text.endswith("\n"))


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        _write(self.root, "t", "zia_url_categories", {
            "blocked_social": {"configured_name": "Blocked Social", "urls": []},
            "blocked_ads": {"configured_name": "Blocked Ads", "urls": []},
        })

    def test_resolve_single_match_case_insensitive(self):
        hits = operate.resolve("t", "zia_url_categories", "blocked social",
                               config_root=self.root)
        self.assertEqual(hits, [("blocked_social", "Blocked Social")])

    def test_resolve_substring_returns_all_candidates(self):
        hits = operate.resolve("t", "zia_url_categories", "blocked",
                               config_root=self.root)
        self.assertEqual(sorted(k for k, _ in hits), ["blocked_ads", "blocked_social"])

    def test_resolve_no_match_is_empty(self):
        self.assertEqual(
            operate.resolve("t", "zia_url_categories", "nope", config_root=self.root),
            [])

    def test_resolve_invalid_tenant_refused(self):
        with self.assertRaises(ValueError):
            operate.resolve("../etc", "zia_url_categories", "x", config_root=self.root)

    def test_resolve_location_by_name(self):
        _write(self.root, "t", "zia_location_management", {
            "hq_office": {"name": "HQ Office", "ip_addresses": []},
            "branch_office": {"name": "Branch Office", "ip_addresses": []},
        })
        hits = operate.resolve("t", "zia_location_management", "office",
                               config_root=self.root)
        self.assertEqual(sorted(k for k, _ in hits),
                         ["branch_office", "hq_office"])


class ScalarSetTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        _write(self.root, "t", "zia_url_filtering_rules", {
            "rule_one": {"name": "Rule One", "state": "ENABLED"},
        })
        _write(self.root, "t", "zpa_segment_group", {
            "grp_one": {"name": "Group One", "enabled": True},
        })

    def _path(self, resource_type):
        return os.path.join(self.root, "t", resource_type + operate.CONFIG_SUFFIX)

    def _read(self, resource_type):
        with open(self._path(resource_type), encoding="utf-8") as f:
            return f.read()

    def _items(self, resource_type):
        return json.loads(self._read(resource_type))["items"]

    def test_disable_rule_sets_state(self):
        msg = operate.scalar_set("t", "zia_url_filtering_rules", "rule_one",
                                 "state", "DISABLED", config_root=self.root)
        self.assertTrue(msg.startswith("set"))
        self.assertEqual(
            self._items("zia_url_filtering_rules")["rule_one"]["state"], "DISABLED")

    def test_enable_rule_idempotent(self):
        before = self._read("zia_url_filtering_rules")
        msg = operate.scalar_set("t", "zia_url_filtering_rules", "rule_one",
                                 "state", "ENABLED", config_root=self.root)
        self.assertTrue(msg.startswith("no-op"))
        self.assertEqual(before, self._read("zia_url_filtering_rules"))

    def test_disable_segment_writes_json_false(self):
        operate.scalar_set("t", "zpa_segment_group", "grp_one",
                           "enabled", "false", config_root=self.root)
        self.assertIs(
            self._items("zpa_segment_group")["grp_one"]["enabled"], False)
        self.assertIn('"enabled": false', self._read("zpa_segment_group"))

    def test_enable_segment_via_ENABLED_token(self):
        operate.scalar_set("t", "zpa_segment_group", "grp_one",
                           "enabled", "false", config_root=self.root)
        operate.scalar_set("t", "zpa_segment_group", "grp_one",
                           "enabled", "ENABLED", config_root=self.root)
        self.assertIs(
            self._items("zpa_segment_group")["grp_one"]["enabled"], True)

    def test_bad_value_refused_lists_allowed(self):
        with self.assertRaises(ValueError) as cm:
            operate.scalar_set("t", "zia_url_filtering_rules", "rule_one",
                               "state", "ENABLE", config_root=self.root)
        self.assertIn("ENABLED", str(cm.exception))
        self.assertIn("DISABLED", str(cm.exception))

    def test_state_enum_refuses_bool_tokens(self):
        # The enum `state` allowlist (_STATE) has no 'true'/'false' keys, so the
        # bool vocabulary stays provably separate from the enum vocabulary.
        for bad in ("true", "false"):
            with self.assertRaises(ValueError) as cm:
                operate.scalar_set("t", "zia_url_filtering_rules", "rule_one",
                                   "state", bad, config_root=self.root)
            self.assertIn("ENABLED", str(cm.exception))
            self.assertIn("DISABLED", str(cm.exception))

    def test_nonallowlisted_field_refused(self):
        with self.assertRaises(ValueError) as cm:
            operate.scalar_set("t", "zia_url_filtering_rules", "rule_one",
                               "action", "BLOCK", config_root=self.root)
        self.assertIn("state", str(cm.exception))

    def test_nonallowlisted_type_refused(self):
        _write(self.root, "t", "zia_location_management", {
            "hq_office": {"name": "HQ Office", "state": "British Columbia"},
        })
        with self.assertRaises(ValueError):
            operate.scalar_set("t", "zia_location_management", "hq_office",
                               "state", "DISABLED", config_root=self.root)

    def test_set_list_field_refused(self):
        _write(self.root, "t", "zpa_application_segment", {
            "seg_one": {"name": "Seg One", "enabled": ["a"]},
        })
        with self.assertRaises(ValueError) as cm:
            operate.scalar_set("t", "zpa_application_segment", "seg_one",
                               "enabled", "false", config_root=self.root)
        self.assertIn("use add/remove, not set", str(cm.exception))

    def test_unknown_key_refused_with_candidates(self):
        with self.assertRaises(ValueError) as cm:
            operate.scalar_set("t", "zia_url_filtering_rules", "Rule One",
                               "state", "DISABLED", config_root=self.root)
        self.assertIn("rule_one", str(cm.exception))

    def test_invalid_tenant_refused(self):
        for bad in ("../etc", "..", ".", "a/b"):
            with self.assertRaises(ValueError):
                operate.scalar_set(bad, "zia_url_filtering_rules", "rule_one",
                                   "state", "DISABLED", config_root=self.root)

    def test_scalar_write_is_canonical(self):
        operate.scalar_set("t", "zia_url_filtering_rules", "rule_one",
                           "state", "DISABLED", config_root=self.root)
        text = self._read("zia_url_filtering_rules")
        self.assertEqual(text, render_tfvars(json.loads(text)["items"]))
        self.assertTrue(text.endswith("\n"))


if __name__ == "__main__":
    unittest.main()
