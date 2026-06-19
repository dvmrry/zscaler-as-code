"""Tests for tools/deployment_config.py -- the single reader for the committable
deployment.json config. Stdlib only, Python 3.6 floor.
"""
import json
import os
import tempfile
import unittest

from tools import deployment_config as dc


def _write(root, obj):
    with open(os.path.join(root, dc.CONFIG_FILE), "w", encoding="utf-8") as f:
        f.write(json.dumps(obj) + "\n")


class DeploymentConfigTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def test_defaults_when_absent(self):
        self.assertEqual(dc.overlay_dir(self.root), "_local")
        self.assertEqual(dc.load(self.root)["overlay"], "_local")

    def test_overlay_override(self):
        _write(self.root, {"overlay": "acme-corp"})
        self.assertEqual(dc.overlay_dir(self.root), "acme-corp")

    def test_comment_keys_ignored(self):
        _write(self.root, {"$note": "a comment", "overlay": "acme-corp"})
        loaded = dc.load(self.root)
        self.assertEqual(loaded["overlay"], "acme-corp")
        self.assertNotIn("$note", loaded)

    def test_extra_keys_preserved(self):
        # The config is extensible: a deployment may add its own pointers.
        _write(self.root, {"overlay": "ov", "future_pointer": "x/y"})
        self.assertEqual(dc.load(self.root)["future_pointer"], "x/y")

    def test_empty_overlay_falls_back_to_default(self):
        _write(self.root, {"overlay": ""})
        self.assertEqual(dc.overlay_dir(self.root), "_local")


if __name__ == "__main__":
    unittest.main()
