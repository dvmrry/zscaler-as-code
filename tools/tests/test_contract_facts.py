import json
import os
import shutil
import tempfile
import unittest

from tools import contract_facts


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")


def _sample_report():
    return {
        "product": "zpa",
        "contract_json": "vendor/zscaler-api-specs/automate-zscaler/zpa-api-reference.json",
        "resources": [
            {
                "resource": "app_connector_group",
                "method": "POST",
                "path": "/zpa/mgmtconfig/v1/admin/customers/:customerId/appConnectorGroup",
                "counts": {"contract": 39, "tf": 26, "go": 41},
                "presence": {
                    "contract_unmatched_in_tf": [
                        "creationTime",
                        "upgradePriority",
                    ],
                    "contract_only_vs_go": ["ipAcl"],
                    "go_only_vs_contract": ["enrollmentCertId", "readOnly"],
                },
                "type_drift": [
                    {"field": "id", "contract": "int64", "go": "string"}
                ],
                "required_drift": [
                    {
                        "field": "latitude",
                        "contract_required": False,
                        "tf_required": True,
                        "direction": "tf_stricter",
                    }
                ],
                "readonly": [
                    {
                        "field": "versionProfileName",
                        "tf_computed": True,
                        "agree": True,
                    }
                ],
                "enum": {
                    "match": [],
                    "value_conflict": [
                        {
                            "field": "dnsMode",
                            "contract": ["A"],
                            "tf": ["B"],
                        }
                    ],
                    "one_sided": [],
                },
            },
            {
                "resource": "application_server",
                "method": "POST",
                "path": "/zpa/mgmtconfig/v1/admin/customers/:customerId/server",
                "counts": {"contract": 12, "tf": 8, "go": 12},
                "presence": {},
                "type_drift": [],
                "required_drift": [],
                "readonly": [],
                "enum": {"match": [], "value_conflict": [], "one_sided": []},
            },
            {
                "resource": "unmanaged",
                "method": "POST",
                "path": "/zpa/mgmtconfig/v1/admin/customers/:customerId/unmanaged",
                "counts": {"contract": 1, "tf": 0, "go": 1},
                "presence": {"contract_unmatched_in_tf": ["field"]},
            },
        ],
    }


class ContractFactsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.overrides = os.path.join(self.tmp, "overrides")
        os.makedirs(self.overrides)
        _write_json(os.path.join(self.overrides, "zpa_app_connector_group.json"), {
            "acknowledged_drops": [
                "selected_upgrade_priority",
                "upgrade_priority",
            ],
            "drop_if_default": {"microtenant_id": "0"},
            "drops": ["creation_time"],
            "renames": {"signing_cert_id": "enrollment_cert_id"},
        })
        self.registry = {
            "zpa_app_connector_group": {"generate": True, "product": "zpa"},
            "zpa_application_server": {"generate": True, "product": "zpa"},
            "zia_url_categories": {"generate": True, "product": "zia"},
        }

    def test_snake_normalizes_contract_fields(self):
        self.assertEqual(contract_facts._snake("upgradePriority"),
                         "upgrade_priority")
        self.assertEqual(contract_facts._snake("versionProfileName"),
                         "version_profile_name")
        self.assertEqual(contract_facts._snake("ipAcl"), "ip_acl")

    def test_render_report_cross_checks_override_paths(self):
        text = contract_facts.render_report(
            _sample_report(), registry=self.registry,
            overrides_dir=self.overrides)
        self.assertIn("# Contract facts: zpa", text)
        self.assertIn("Managed resources in scope: 2", text)
        self.assertIn("## zpa_app_connector_group", text)
        self.assertIn("creation_time", text)
        self.assertIn("upgrade_priority", text)
        self.assertIn(
            "directly supported: creation_time, upgrade_priority", text)
        self.assertIn(
            "not directly in report: microtenant_id, selected_upgrade_priority",
            text)
        self.assertIn(
            "report candidates not currently acknowledged: id, ip_acl, "
            "read_only, version_profile_name",
            text)
        self.assertNotIn("enrollment_cert_id, id", text)
        self.assertIn(
            "latitude: contract required=False, TF required=True "
            "(tf_stricter)",
            text)
        self.assertIn("dns_mode: contract ['A'] vs TF ['B']", text)

    def test_selector_filters_to_one_resource(self):
        text = contract_facts.render_report(
            _sample_report(), registry=self.registry,
            overrides_dir=self.overrides,
            selectors=["zpa_application_server"])
        self.assertIn("Managed resources in scope: 1", text)
        self.assertIn("## zpa_application_server", text)
        self.assertNotIn("## zpa_app_connector_group", text)

    def test_product_selector_matches_product_prefix(self):
        text = contract_facts.render_report(
            _sample_report(), registry=self.registry,
            overrides_dir=self.overrides,
            selectors=["zpa"])
        self.assertIn("Managed resources in scope: 2", text)

    def test_load_report_rejects_wrong_shape(self):
        path = os.path.join(self.tmp, "bad.json")
        _write_json(path, {"product": "zpa", "resources": {}})
        with self.assertRaises(ValueError):
            contract_facts.load_report(path)

    def test_resource_type_for_cloud_connector_stays_distinct_from_zcc(self):
        self.assertEqual(
            contract_facts.resource_type_for("zcloudconnector", "forwarding_profile"),
            "zcloudconnector_forwarding_profile")
        self.assertEqual(
            contract_facts.resource_type_for("cloud-connector", "traffic_rule"),
            "cloud_connector_traffic_rule")


if __name__ == "__main__":
    unittest.main()
