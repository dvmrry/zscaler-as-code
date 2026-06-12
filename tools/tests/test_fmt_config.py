"""Tests for make fmt-config (tools/fmt_config.py).

This module had ZERO coverage and shipped a crash on every invocation
(binary-mode open with an encoding kwarg) that 500+ other tests never
touched — it is also the exact command lint's remediation strings tell
the operator to run. These tests pin the full e2e behavior.

Uses a gitignored scratch tenant under config/ (same pattern as
test_apply_chain's tmpchaintest) because the tools resolve registry and
schema paths relative to the repo root cwd.
"""
import json
import os
import shutil
import unittest

from tools.fmt_config import main
from tools.transform import render_tfvars

TENANT = "tmpfmttest"
CONFIG_DIR = os.path.join("config", TENANT)


class FmtConfigTest(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(CONFIG_DIR, ignore_errors=True)
        os.makedirs(CONFIG_DIR)
        self.path = os.path.join(
            CONFIG_DIR, "zpa_segment_group.auto.tfvars.json")

    def tearDown(self):
        shutil.rmtree(CONFIG_DIR, ignore_errors=True)

    def test_non_canonical_file_is_rewritten(self):
        items = {"k": {"name": "Group", "enabled": True}}
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"items": items}, indent=4))  # wrong indent
        self.assertEqual(main([TENANT]), 0)
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), render_tfvars(items))

    def test_bom_is_stripped(self):
        items = {"k": {"name": "G"}}
        with open(self.path, "wb") as f:
            f.write(b"\xef\xbb\xbf" + render_tfvars(items).encode("utf-8"))
        self.assertEqual(main([TENANT]), 0)
        with open(self.path, "rb") as f:
            raw = f.read()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(raw.decode("utf-8"), render_tfvars(items))

    def test_canonical_file_untouched_and_idempotent(self):
        items = {"k": {"name": "G"}}
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(render_tfvars(items))
        before = os.stat(self.path).st_mtime_ns
        self.assertEqual(main([TENANT]), 0)
        self.assertEqual(os.stat(self.path).st_mtime_ns, before)

    def test_missing_tenant_exits_1(self):
        shutil.rmtree(CONFIG_DIR, ignore_errors=True)
        self.assertEqual(main([TENANT]), 1)

    def test_usage_exits_2(self):
        self.assertEqual(main([]), 2)


if __name__ == "__main__":
    unittest.main()
