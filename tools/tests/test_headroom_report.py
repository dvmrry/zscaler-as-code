"""Tests for provider-vs-adoption reporting."""
import unittest

from tools import adoption_status, headroom_report


class AdoptionStatusTest(unittest.TestCase):
    def test_real_status_file_validates(self):
        data = adoption_status.load_status()
        self.assertIn("zia_activation_status", data["dispositions"])
        self.assertEqual(
            adoption_status.known_hold_paths("zia_dlp_web_rules", data),
            ["uc_template_id"],
        )

    def test_invalid_status_rejected(self):
        bad = {"dispositions": {"x": {"status": "maybe", "reason": "no"}}}
        with self.assertRaises(ValueError):
            adoption_status.validate_status(bad, "status.json")


class HeadroomReportTest(unittest.TestCase):
    def test_managed_resource_shows_fetch_and_known_hold(self):
        text = headroom_report.render_report(selectors=["zia_dlp_web_rules"])
        self.assertIn("Provider resources in scope: 1", text)
        self.assertIn("| `zia_dlp_web_rules` | `zia` | `managed-fetch` |", text)
        self.assertIn("fetch zia/webDlpRules", text)
        self.assertIn("known hold: uc_template_id (zia-70)", text)

    def test_dispositioned_resource_not_reported_as_adopted(self):
        text = headroom_report.render_report(selectors=["zia_activation_status"])
        self.assertIn("Provider resources in scope: 1", text)
        self.assertIn("action-not-resource", text)
        self.assertIn("Activation is an action", text)

    def test_unmanaged_provider_resource_is_module_ready_headroom(self):
        text = headroom_report.render_report(selectors=["zia_admin_roles"])
        self.assertIn("Provider resources in scope: 1", text)
        self.assertIn("module-ready", text)
        self.assertIn("not adopted", text)

    def test_product_selector_summarizes_many_rows(self):
        text = headroom_report.render_report(selectors=["zpa"])
        self.assertIn("Selector: zpa", text)
        self.assertIn("managed-fetch", text)
        self.assertIn("module-ready", text)


if __name__ == "__main__":
    unittest.main()
