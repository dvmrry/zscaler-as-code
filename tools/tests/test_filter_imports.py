"""Tests for tools/filter_imports.py — state-aware import filtering."""
import unittest

from tools.filter_imports import filter_imports

IMPORTS = (
    'import {\n  to = module.x.x.this["a"]\n  id = "1"\n}\n\n'
    'import {\n  to = module.x.x.this["b"]\n  id = "2"\n}\n'
)


class FilterImportsTest(unittest.TestCase):
    def test_managed_addresses_dropped(self):
        text, kept, skipped = filter_imports(
            IMPORTS, ['module.x.x.this["a"]'])
        self.assertEqual((kept, skipped), (1, 1))
        self.assertIn('"b"', text)
        self.assertNotIn('"a"]', text)

    def test_empty_state_keeps_everything(self):
        text, kept, skipped = filter_imports(IMPORTS, [])
        self.assertEqual((kept, skipped), (2, 0))
        self.assertEqual(text.count("import {"), 2)

    def test_all_managed_yields_empty(self):
        text, kept, skipped = filter_imports(
            IMPORTS, ['module.x.x.this["a"]', 'module.x.x.this["b"]'])
        self.assertEqual((kept, skipped), (0, 2))
        self.assertEqual(text, "")


if __name__ == "__main__":
    unittest.main()
