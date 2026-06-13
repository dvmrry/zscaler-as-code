"""Pins `make transform`'s RESOURCE selector (DAV-8 P1).

Scoped drift fetches a subset but used to transform UNSCOPED, so stale
JSON already in pulls/<tenant>/ could be re-transformed into stale
backfill. transform now accepts the same selector semantics as fetch
(exact type, a zia/zpa/zcc product token, or a multi-token list); these
tests drive the real Makefile against an EMPTY input dir, so every
SELECTED type prints a `skip … (no … .json)` line and nothing is
written — which is exactly the set we assert on.
"""
import os
import shutil
import subprocess
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))

HAVE_MAKE = shutil.which("make") and shutil.which("python3")
TENANT = "tmptransformscope"


@unittest.skipUnless(HAVE_MAKE, "make and python3 required")
class TransformScopeTest(unittest.TestCase):
    def setUp(self):
        self.empty = tempfile.mkdtemp(prefix="emptypull-")
        self.addCleanup(shutil.rmtree, self.empty, True)
        # nothing should ever be written (all inputs absent), but guard anyway
        self.addCleanup(shutil.rmtree,
                        os.path.join(REPO_ROOT, "config", TENANT), True)
        self.addCleanup(shutil.rmtree,
                        os.path.join(REPO_ROOT, "imports", TENANT), True)

    def _run(self, resource=None):
        cmd = ["make", "transform", "IN=" + self.empty, "TENANT=" + TENANT]
        if resource is not None:
            cmd.append("RESOURCE=" + resource)
        out = subprocess.run(cmd, cwd=REPO_ROOT, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT)
        return out.returncode, out.stdout.decode("utf-8", "replace")

    def _skipped(self, output):
        return sorted(line.split()[1] for line in output.splitlines()
                      if line.startswith("skip "))

    def test_product_token_scopes_to_that_product(self):
        code, out = self._run("zia")
        skipped = self._skipped(out)
        self.assertTrue(skipped, out)
        self.assertTrue(all(t.startswith("zia_") for t in skipped), skipped)
        self.assertFalse(any(t.startswith(("zpa_", "zcc_")) for t in skipped))

    def test_exact_type_scopes_to_one(self):
        code, out = self._run("zia_url_categories")
        self.assertEqual(self._skipped(out), ["zia_url_categories"], out)

    def test_multi_token_selector(self):
        skipped = self._skipped(self._run("zia zpa")[1])
        self.assertTrue(all(t.startswith(("zia_", "zpa_")) for t in skipped), skipped)
        self.assertTrue(any(t.startswith("zia_") for t in skipped))
        self.assertTrue(any(t.startswith("zpa_") for t in skipped))
        self.assertFalse(any(t.startswith("zcc_") for t in skipped))

    def test_unscoped_includes_every_product(self):
        skipped = self._skipped(self._run()[1])
        self.assertTrue(any(t.startswith("zia_") for t in skipped), skipped)
        self.assertTrue(any(t.startswith("zpa_") for t in skipped))
        self.assertTrue(any(t.startswith("zcc_") for t in skipped))

    def test_nothing_is_written_when_inputs_absent(self):
        self._run("zia")
        self.assertFalse(os.path.exists(os.path.join(REPO_ROOT, "config", TENANT)))


if __name__ == "__main__":
    unittest.main()
