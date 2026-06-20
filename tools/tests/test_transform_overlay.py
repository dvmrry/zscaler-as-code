import os
import unittest


class TransformOverlayTest(unittest.TestCase):
    def test_transform_uses_resolver_for_output_dirs(self):
        # Source-level guard: transform must build output paths from the resolver,
        # not a literal os.path.join("config"|"imports", tenant, ...).
        src = open(os.path.join("tools", "transform.py"), encoding="utf-8").read()
        self.assertNotIn('os.path.join("config", tenant', src)
        self.assertNotIn('os.path.join("imports", tenant', src)
        self.assertIn("deployment.config_dir", src)
        self.assertIn("deployment.imports_dir", src)


if __name__ == "__main__":
    unittest.main()
