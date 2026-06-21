import os
import subprocess
import sys
import unittest

PINNED = ("modules/", "envs/demo/", "imports/demo/", "tools/tests/", "tools/schema-extract/")


class ValidatePinTest(unittest.TestCase):
    def test_every_tracked_tf_is_pinned_or_overlay(self):
        tf = subprocess.check_output(["git", "ls-files", "*.tf"]).decode().split()
        overlay = subprocess.check_output(
            [sys.executable, "-m", "tools.deployment", "overlay"]).decode().strip()
        stray = [p for p in tf
                 if not p.startswith(PINNED)
                 and not (overlay != "." and p.startswith(overlay + "/"))]
        self.assertEqual(stray, [], "tracked .tf neither pinned nor under overlay: %s" % stray)

    def test_makefile_validate_is_pinned_not_bare_recursive(self):
        with open("Makefile", encoding="utf-8") as handle:
            src = handle.read()
        self.assertNotIn("fmt -check -recursive\n", src)  # no bare recursive walk
        for d in ("modules", "envs/demo", "imports/demo", "tools/tests", "tools/schema-extract"):
            self.assertIn(d, src)


if __name__ == "__main__":
    unittest.main()
