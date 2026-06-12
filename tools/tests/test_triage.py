"""Tests for the drop-report triage classifier (tools/triage.py).

The contract: the dangerous class (SYNONYM — the signingCertId shape)
is always flagged for eyes and never auto-acknowledged; the safe
classes are provably safe by construction; APPLY writes only the safe
ones. All offline — the SDK lane is exercised with injected text.
"""
import json
import os
import shutil
import tempfile
import unittest

import tools.triage as triage
import tools.transform as transform_mod
from tools.triage import SAFE_CLASSES, classify


class ClassifyHistoryTest(unittest.TestCase):
    """Every classification is a regression pin from a real incident or
    the estate-wide triage."""

    def test_signing_cert_is_synonym_never_safe(self):
        klass, why = classify("zpa_app_connector_group", "signing_cert_id")
        self.assertEqual(klass, "SYNONYM")
        self.assertNotIn(klass, SAFE_CLASSES)
        self.assertIn("enrollment_cert_id", why)

    def test_cbi_profile_id_is_synonym(self):
        klass, why = classify("zia_url_filtering_rules", "cbi_profile_id")
        self.assertEqual(klass, "SYNONYM")
        self.assertIn("cbi_profile", why)

    def test_nested_decoration_is_safe(self):
        for rt, path in (
                ("zpa_application_segment", "server_groups[].description"),
                ("zia_ssl_inspection_rules", "device_groups.name"),
                ("zpa_server_group", "app_connector_groups[].version_profile_id")):
            klass, _ = classify(rt, path)
            self.assertEqual(klass, "DECORATION", path)

    def test_nested_description_not_matched_against_top_level(self):
        # synonym comparison is scoped per nesting level: a nested
        # 'description' must not trip on the resource's own description
        klass, _ = classify("zpa_application_segment",
                            "server_groups[].description")
        self.assertNotEqual(klass, "SYNONYM")

    def test_metadata_vocabulary_and_computed_only(self):
        self.assertEqual(
            classify("zpa_application_segment", "creation_time")[0],
            "METADATA")
        self.assertEqual(classify("zia_url_categories", "val")[0],
                         "METADATA")

    def test_sdk_modeled_with_text_unknown_without(self):
        with_text = classify("zia_url_filtering_rules", "capture_pcap",
                             'X json:"capturePcap,omitempty"')
        self.assertEqual(with_text[0], "SDK")
        offline = classify("zia_url_filtering_rules", "capture_pcap", None)
        self.assertEqual(offline[0], "UNKNOWN")
        self.assertIn("capturePcap", offline[1])

    def test_conservative_synonym_on_related_settings(self):
        # exclude_src_countries vs source_countries: a real distinct
        # feature, but the classifier must err toward eyes
        klass, _ = classify("zia_url_filtering_rules",
                            "exclude_src_countries")
        self.assertEqual(klass, "SYNONYM")


class MainFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.pulls = os.path.join(self.tmp, "pulls")
        os.makedirs(self.pulls)
        # private override dir so APPLY never touches the repo's files
        self.ovdir = os.path.join(self.tmp, "overrides")
        os.makedirs(self.ovdir)
        src = os.path.join("tools", "overrides", "zpa_application_segment.json")
        shutil.copy(src, self.ovdir)
        self.old_ovdir = transform_mod.OVERRIDES_DIR
        transform_mod.OVERRIDES_DIR = self.ovdir
        self.old_triage_ovdir = triage.OVERRIDES_DIR
        triage.OVERRIDES_DIR = self.ovdir
        self.old_fetch = triage.fetch_sdk_text
        triage.fetch_sdk_text = lambda rt: None
        os.environ.pop("APPLY", None)

    def tearDown(self):
        transform_mod.OVERRIDES_DIR = self.old_ovdir
        triage.OVERRIDES_DIR = self.old_triage_ovdir
        triage.fetch_sdk_text = self.old_fetch
        os.environ.pop("APPLY", None)
        shutil.rmtree(self.tmp)

    def _write_pull(self, items):
        with open(os.path.join(self.pulls, "zpa_application_segment.json"),
                  "w", encoding="utf-8") as f:
            json.dump(items, f)

    def _run(self):
        import io
        import sys
        old_out, sys.stdout = sys.stdout, io.StringIO()
        try:
            code = triage.main([self.pulls])
            return code, sys.stdout.getvalue()
        finally:
            sys.stdout = old_out

    def test_safe_only_applies_and_exits_0(self):
        self._write_pull([{"id": "1", "name": "S", "domainNames": ["a.t"],
                           "brandNewAuditStamp": "x"}])
        os.environ["APPLY"] = "1"
        code, out = self._run()
        # brand_new_audit_stamp: no synonym, not metadata, offline SDK
        # -> UNKNOWN -> worklist, exit 4 (proves UNKNOWN is never applied)
        self.assertEqual(code, 4)
        ov = json.load(open(os.path.join(
            self.ovdir, "zpa_application_segment.json"), encoding="utf-8"))
        self.assertNotIn("brand_new_audit_stamp",
                         ov.get("acknowledged_drops") or [])

    def test_decoration_applies_and_quiets(self):
        self._write_pull([{"id": "1", "name": "S", "domainNames": ["a.t"],
                           "serverGroups": [{"id": "g1",
                                             "upgradeDay": "SUNDAY"}]}])
        os.environ["APPLY"] = "1"
        code, out = self._run()
        self.assertEqual(code, 0)
        self.assertIn("DECORATION", out)
        ov = json.load(open(os.path.join(
            self.ovdir, "zpa_application_segment.json"), encoding="utf-8"))
        self.assertIn("server_groups[].upgrade_day",
                      ov.get("acknowledged_drops") or [])

    def test_dry_run_writes_nothing(self):
        self._write_pull([{"id": "1", "name": "S", "domainNames": ["a.t"],
                           "serverGroups": [{"id": "g1",
                                             "upgradeDay": "SUNDAY"}]}])
        before = open(os.path.join(
            self.ovdir, "zpa_application_segment.json"),
            encoding="utf-8").read()
        code, out = self._run()
        self.assertEqual(code, 0)
        self.assertIn("dry run", out)
        after = open(os.path.join(
            self.ovdir, "zpa_application_segment.json"),
            encoding="utf-8").read()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
