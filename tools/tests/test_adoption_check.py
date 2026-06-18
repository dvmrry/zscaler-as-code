"""Tests for targeted adoption acceptance checks."""
import json
import os
import shutil
import tempfile
import unittest

from tools import adoption_check
from tools.adoption_status import load_status


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")


class AdoptionCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.status = load_status()

    def test_known_hold_is_accepted(self):
        _write_json(os.path.join(self.tmp, "zia_dlp_web_rules.json"), [{
            "id": 1,
            "name": "DLP",
            "order": 1,
            "ucTemplateId": 7,
        }])
        result = adoption_check.check_resource(
            "zia_dlp_web_rules", "tenant", self.tmp, self.status, write=False)
        self.assertEqual(result["state"], "known-hold")
        self.assertEqual(result["drops"], ["uc_template_id"])
        self.assertEqual(result["unexpected"], [])

    def test_known_hold_render_names_issue(self):
        _write_json(os.path.join(self.tmp, "zia_dlp_web_rules.json"), [{
            "id": 1,
            "name": "DLP",
            "order": 1,
            "ucTemplateId": 7,
        }])
        result = adoption_check.check_resource(
            "zia_dlp_web_rules", "tenant", self.tmp, self.status, write=False)
        self.assertEqual(
            adoption_check.render_result(result),
            "KNOWN-HOLD zia_dlp_web_rules: uc_template_id (zia-70)")

    def test_unexpected_drop_fails(self):
        _write_json(os.path.join(self.tmp, "zpa_segment_group.json"), [{
            "id": "1",
            "name": "Segment Group",
            "brandNewApiField": "x",
        }])
        result = adoption_check.check_resource(
            "zpa_segment_group", "tenant", self.tmp, self.status, write=False)
        self.assertEqual(result["state"], "unexpected-drops")
        self.assertIn("brand_new_api_field", result["unexpected"])

    def test_missing_pull_is_loud(self):
        result = adoption_check.check_resource(
            "zpa_segment_group", "tenant", self.tmp, self.status, write=False)
        self.assertEqual(result["state"], "missing-pull")
        self.assertIn("zpa_segment_group.json", result["path"])

    def test_expand_resources_refuses_non_fetch_resource(self):
        with self.assertRaises(ValueError):
            adoption_check.expand_resources(["zpa_policy_access_rule_reorder"])

    def test_check_resource_refuses_non_fetch_resource(self):
        with self.assertRaises(ValueError):
            adoption_check.check_resource(
                "zpa_policy_access_rule_reorder", "tenant", self.tmp,
                self.status, write=False)

    def test_tenant_guard(self):
        with self.assertRaises(ValueError):
            adoption_check.validate_tenant("../tenant")


if __name__ == "__main__":
    unittest.main()
