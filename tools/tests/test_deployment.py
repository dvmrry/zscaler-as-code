import json
import os
import unittest

from tools import deployment


class DeploymentResolverTest(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = self.id().replace(".", "_") + ".tmpdir"
        os.makedirs(self._tmp, exist_ok=True)
        os.chdir(self._tmp)

    def tearDown(self):
        os.chdir(self._cwd)
        import shutil
        shutil.rmtree(os.path.join(self._cwd, self._tmp), ignore_errors=True)

    def _write(self, obj_or_text):
        with open("deployment.json", "w", encoding="utf-8") as f:
            f.write(obj_or_text if isinstance(obj_or_text, str) else json.dumps(obj_or_text))

    def test_absent_resolves_root(self):
        self.assertEqual(deployment.overlay(), ".")
        self.assertEqual(deployment.tenant_root("anything"), ".")
        self.assertEqual(deployment.config_dir("acme"), os.path.join("config", "acme"))

    def test_empty_file_resolves_root(self):
        self._write("   \n")
        self.assertEqual(deployment.overlay(), ".")

    def test_dot_overlay_resolves_root(self):
        self._write({"overlay": "."})
        self.assertEqual(deployment.tenant_root("acme"), ".")

    def test_overlay_set_real_tenant_under_overlay(self):
        self._write({"overlay": "_local"})
        self.assertEqual(deployment.overlay(), "_local")
        self.assertEqual(deployment.tenant_root("acme"), "_local")
        self.assertEqual(deployment.config_dir("acme"), os.path.join("_local", "config", "acme"))
        self.assertEqual(deployment.imports_dir("acme"), os.path.join("_local", "imports", "acme"))
        self.assertEqual(deployment.envs_dir("acme"), os.path.join("_local", "envs", "acme"))
        self.assertEqual(deployment.lookups_dir("acme"), os.path.join("_local", "lookups", "acme"))

    def test_demo_always_root_even_with_overlay(self):
        self._write({"overlay": "_local"})
        self.assertEqual(deployment.tenant_root("demo"), ".")
        self.assertEqual(deployment.config_dir("demo"), os.path.join("config", "demo"))
        self.assertEqual(deployment.lookups_dir("demo"), os.path.join("lookups", "demo"))

    def test_pulls_always_root(self):
        self._write({"overlay": "_local"})
        self.assertEqual(deployment.pulls_dir("acme"), os.path.join("pulls", "acme"))

    def test_comment_keys_ignored(self):
        self._write({"$note": "hi", "overlay": "_local"})
        self.assertEqual(deployment.overlay(), "_local")

    def test_malformed_raises(self):
        self._write("{ not json")
        with self.assertRaises(ValueError):
            deployment.overlay()

    def test_prefix_is_repo_relative_string(self):
        self._write({"overlay": "_local"})
        self.assertEqual(deployment.config_prefix("acme"), os.path.join("_local", "config", "acme"))
        self.assertEqual(deployment.lookups_prefix("acme"), os.path.join("_local", "lookups", "acme"))


if __name__ == "__main__":
    unittest.main()
