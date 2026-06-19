"""Tests for tools.lookup ID-readability sidecars and explain output."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from tools import lookup
from tools.transform import main as transform_main

REPO_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
HAVE_MAKE = shutil.which("make") and shutil.which("python3")


def _write_json(path, data):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")


class LookupBuildTest(unittest.TestCase):
    def test_build_lookup_uses_configured_name(self):
        items = [
            {"id": "CUSTOM_02", "configured_name": "Beta"},
            {"id": "CUSTOM_01", "configured_name": "Alpha"},
        ]
        self.assertEqual(
            lookup.build_lookup(items, "configured_name"),
            {"CUSTOM_01": "Alpha", "CUSTOM_02": "Beta"},
        )

    def test_build_lookup_missing_name_is_unknown(self):
        items = [
            {"id": "CUSTOM_01"},
            {"id": "CUSTOM_02", "configured_name": ""},
            {"id": "CUSTOM_03", "configured_name": "   "},
            {"id": "CUSTOM_04", "configured_name": 123},
        ]
        self.assertEqual(
            lookup.build_lookup(items, "configured_name"),
            {
                "CUSTOM_01": lookup.UNKNOWN,
                "CUSTOM_02": lookup.UNKNOWN,
                "CUSTOM_03": lookup.UNKNOWN,
                "CUSTOM_04": lookup.UNKNOWN,
            },
        )

    def test_build_lookup_skips_missing_id(self):
        items = [{"configured_name": "No ID"}, {"id": "", "configured_name": "Empty"}]
        self.assertEqual(lookup.build_lookup(items, "configured_name"), {})

    def test_render_lookup_is_sorted_with_trailing_newline(self):
        self.assertEqual(
            lookup.render_lookup({"CUSTOM_02": "Beta", "CUSTOM_01": "Alpha"}),
            '{\n  "CUSTOM_01": "Alpha",\n  "CUSTOM_02": "Beta"\n}\n',
        )


class LookupTransformTest(unittest.TestCase):
    TENANT = "tmplookuptest"

    def setUp(self):
        self.addCleanup(shutil.rmtree, os.path.join(REPO_ROOT, "config", self.TENANT), True)
        self.addCleanup(shutil.rmtree, os.path.join(REPO_ROOT, "imports", self.TENANT), True)

    def test_transform_writes_url_category_lookup(self):
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "zia_url_categories.json")
            _write_json(src, [
                {"id": "CUSTOM_02", "configuredName": "Beta"},
                {"id": "CUSTOM_01", "configuredName": "Alpha"},
            ])

            self.assertEqual(transform_main(["zia_url_categories", src, self.TENANT]), 0)

            lookup_file = os.path.join(
                REPO_ROOT, "config", self.TENANT, "zia_url_categories.lookup.json")
            with open(lookup_file, encoding="utf-8") as f:
                self.assertEqual(
                    f.read(),
                    '{\n  "CUSTOM_01": "Alpha",\n  "CUSTOM_02": "Beta"\n}\n',
                )

            config_file = os.path.join(
                REPO_ROOT, "config", self.TENANT,
                "zia_url_categories.auto.tfvars.json")
            with open(config_file, encoding="utf-8") as f:
                data = json.load(f)
            self.assertIn("items", data)
            self.assertNotIn("lookup", data)

    def test_transform_for_referrer_does_not_touch_referent_lookup(self):
        lookup_dir = os.path.join(REPO_ROOT, "config", self.TENANT)
        os.makedirs(lookup_dir, exist_ok=True)
        lookup_file = os.path.join(lookup_dir, "zia_url_categories.lookup.json")
        original = '{"CUSTOM_01": "Alpha"}\n'
        with open(lookup_file, "w", encoding="utf-8") as f:
            f.write(original)

        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "zia_url_filtering_rules.json")
            _write_json(src, [{
                "id": 1,
                "name": "Rule One",
                "order": 1,
                "protocols": ["ANY_RULE"],
                "urlCategories": ["CUSTOM_01"],
            }])

            self.assertEqual(
                transform_main(["zia_url_filtering_rules", src, self.TENANT]), 0)

        with open(lookup_file, encoding="utf-8") as f:
            self.assertEqual(f.read(), original)


class LookupExplainTest(unittest.TestCase):
    def setUp(self):
        self.config_root = tempfile.mkdtemp(prefix="lookup-config-")
        self.addCleanup(shutil.rmtree, self.config_root, True)

    def _write_config(self, tenant, resource_type, items):
        _write_json(
            os.path.join(self.config_root, tenant,
                         resource_type + lookup.CONFIG_SUFFIX),
            {"items": items},
        )

    def _write_lookup(self, tenant, referent, mapping):
        _write_json(
            os.path.join(self.config_root, tenant,
                         referent + lookup.LOOKUP_SUFFIX),
            mapping,
        )

    def test_explain_resolves_custom_system_and_unknown(self):
        self._write_config("t", "zia_url_filtering_rules", {
            "block_risky": {
                "name": "Block Risky",
                "url_categories": ["CUSTOM_01", "GAMBLING", "CUSTOM_99"],
            },
        })
        self._write_lookup("t", "zia_url_categories", {
            "CUSTOM_01": "Engineering Wiki",
        })

        out = lookup.render_explain(
            "t", ["zia_url_filtering_rules"], config_root=self.config_root)

        self.assertIn("zia_url_filtering_rules\n", out)
        self.assertIn("  Block Risky\n", out)
        self.assertIn("Engineering Wiki (CUSTOM_01)", out)
        self.assertIn("GAMBLING (GAMBLING)", out)
        self.assertIn("%s (CUSTOM_99)" % lookup.UNKNOWN, out)

    def test_explain_groups_duplicate_names(self):
        self._write_config("t", "zia_url_filtering_rules", {
            "r": {
                "name": "R",
                "url_categories": ["CUSTOM_02", "CUSTOM_01"],
            },
        })
        self._write_lookup("t", "zia_url_categories", {
            "CUSTOM_01": "Engineering Wiki",
            "CUSTOM_02": "Engineering Wiki",
        })

        out = lookup.render_explain(
            "t", ["zia_url_filtering_rules"], config_root=self.config_root)

        self.assertIn("Engineering Wiki (CUSTOM_01, CUSTOM_02)", out)

    def test_missing_lookup_file_is_unknown_not_error(self):
        self._write_config("t", "zia_url_filtering_rules", {
            "r": {"name": "R", "url_categories": ["CUSTOM_01"]},
        })
        missing = []
        self.assertEqual(
            lookup.render_explain(
                "t", ["zia_url_filtering_rules"], config_root=self.config_root,
                missing_lookups=missing),
            "zia_url_filtering_rules\n"
            "  R\n"
            "    url_categories: %s (CUSTOM_01)\n" % lookup.UNKNOWN,
        )
        self.assertEqual(missing, ["zia_url_categories"])

    def test_unmanifested_resource_prints_nothing(self):
        self._write_config("t", "zia_url_categories", {
            "category": {"configured_name": "Category"},
        })
        self.assertEqual(
            lookup.render_explain(
                "t", ["zia_url_categories"], config_root=self.config_root),
            "",
        )


class LookupCliTest(unittest.TestCase):
    TENANT = "tmplookupcli"

    def setUp(self):
        self.addCleanup(shutil.rmtree, os.path.join(REPO_ROOT, "config", self.TENANT), True)

    def test_explain_warns_when_lookup_file_is_missing(self):
        _write_json(
            os.path.join(REPO_ROOT, "config", self.TENANT,
                         "zia_url_filtering_rules.auto.tfvars.json"),
            {"items": {
                "r": {"name": "R", "url_categories": ["CUSTOM_01"]},
            }},
        )
        out = io.StringIO()
        err = io.StringIO()

        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = lookup.main(["explain", self.TENANT, "zia_url_filtering_rules"])

        self.assertEqual(code, 0)
        self.assertIn("%s (CUSTOM_01)" % lookup.UNKNOWN, out.getvalue())
        self.assertEqual(
            err.getvalue(),
            "warning: no lookup for zia_url_categories - run transform\n",
        )


@unittest.skipUnless(HAVE_MAKE, "make and python3 required")
class LookupMakeScopeTest(unittest.TestCase):
    TENANT = "tmplookupscope"

    def setUp(self):
        self.pull_dir = tempfile.mkdtemp(prefix="lookuppulls-")
        self.addCleanup(shutil.rmtree, self.pull_dir, True)
        self.addCleanup(shutil.rmtree, os.path.join(REPO_ROOT, "config", self.TENANT), True)
        self.addCleanup(shutil.rmtree, os.path.join(REPO_ROOT, "imports", self.TENANT), True)

    def _make_transform(self, resource):
        cmd = [
            "make",
            "transform",
            "IN=" + self.pull_dir,
            "TENANT=" + self.TENANT,
            "RESOURCE=" + resource,
        ]
        out = subprocess.run(
            cmd, cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return out.returncode, out.stdout.decode("utf-8", "replace")

    def test_make_transform_scope_only_writes_selected_lookup(self):
        _write_json(os.path.join(self.pull_dir, "zia_url_categories.json"), [
            {"id": "CUSTOM_01", "configuredName": "Alpha"},
        ])

        code, out = self._make_transform("zia_url_categories")
        self.assertEqual(code, 0, out)
        lookup_file = os.path.join(
            REPO_ROOT, "config", self.TENANT, "zia_url_categories.lookup.json")
        with open(lookup_file, encoding="utf-8") as f:
            original = f.read()
        self.assertIn('"CUSTOM_01": "Alpha"', original)

        _write_json(os.path.join(self.pull_dir, "zia_url_filtering_rules.json"), [{
            "id": 1,
            "name": "Rule One",
            "order": 1,
            "protocols": ["ANY_RULE"],
            "urlCategories": ["CUSTOM_01"],
        }])

        code, out = self._make_transform("zia_url_filtering_rules")
        self.assertEqual(code, 0, out)
        with open(lookup_file, encoding="utf-8") as f:
            self.assertEqual(f.read(), original)


if __name__ == "__main__":
    unittest.main()
