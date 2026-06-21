import json
import os
import subprocess
import sys
import unittest

PINNED = ("modules/", "envs/demo/", "imports/demo/", "tools/tests/", "tools/schema-extract/")


def _overlay_config():
    overlay = subprocess.check_output(
        [sys.executable, "-m", "tools.deployment", "overlay"]).decode().strip()
    explicit_root_overlay = False
    if os.path.exists("deployment.json"):
        with open("deployment.json", encoding="utf-8") as handle:
            text = handle.read()
        if text.strip():
            data = json.loads(text)
            explicit_root_overlay = data.get("overlay") == "."
    return overlay, explicit_root_overlay


def _stray_tf(tf_paths, overlay, explicit_root_overlay):
    if explicit_root_overlay:
        return []
    return [p for p in tf_paths
            if not p.startswith(PINNED)
            and not (overlay != "." and p.startswith(overlay + "/"))]


class ValidatePinTest(unittest.TestCase):
    def test_every_tracked_tf_is_pinned_or_overlay(self):
        tf = subprocess.check_output(["git", "ls-files", "*.tf"]).decode().split()
        overlay, explicit_root_overlay = _overlay_config()
        stray = _stray_tf(tf, overlay, explicit_root_overlay)
        self.assertEqual(stray, [], "tracked .tf neither pinned nor under overlay: %s" % stray)

    def test_default_root_overlay_still_pins_template_paths(self):
        stray = _stray_tf(["main.tf", "modules/x/main.tf"], ".", False)
        self.assertEqual(stray, ["main.tf"])

    def test_explicit_root_overlay_allows_root_paths(self):
        stray = _stray_tf(["main.tf", "modules/x/main.tf"], ".", True)
        self.assertEqual(stray, [])

    def test_named_overlay_allows_only_overlay_paths(self):
        stray = _stray_tf(["main.tf", "_local/main.tf"], "_local", False)
        self.assertEqual(stray, ["main.tf"])

    def test_makefile_validate_is_pinned_not_bare_recursive(self):
        with open("Makefile", encoding="utf-8") as handle:
            src = handle.read()
        self.assertNotIn("fmt -check -recursive\n", src)  # no bare recursive walk
        for d in ("modules", "envs/demo", "imports/demo", "tools/tests", "tools/schema-extract"):
            self.assertIn(d, src)


if __name__ == "__main__":
    unittest.main()
