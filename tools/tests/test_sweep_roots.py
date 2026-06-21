import os
import shutil
import subprocess
import tempfile
import unittest


def _make_value(var):
    """Return the *expanded* value of a Makefile variable, evaluated by make
    itself (so recursive `=` definitions resolve, not just their raw RHS)."""
    recipe = "__print_var__:\n\t@printf '%%s' '$(%s)'\n" % var
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".mk", delete=False, encoding="utf-8"
    )
    try:
        with handle:
            handle.write(recipe)
        return subprocess.check_output(
            ["make", "--no-print-directory", "-f", "Makefile",
             "-f", handle.name, "__print_var__"],
            stderr=subprocess.DEVNULL,
        ).decode("utf-8")
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass


@unittest.skipUnless(shutil.which("make"), "make not available")
class SweepRootsTest(unittest.TestCase):
    def test_env_roots_collapses_at_default(self):
        # ENV_ROOTS must expand to a single 'envs' token when OVERLAY='.'
        # (no deployment.json) — the no-double-scan invariant from the spec.
        value = _make_value("ENV_ROOTS").strip()
        self.assertEqual(value.split(), ["envs"],
                         "ENV_ROOTS must collapse to one 'envs' token at overlay='.'; got %r" % value)

    def test_drift_detect_uses_resolver(self):
        with open("Makefile", encoding="utf-8") as handle:
            src = handle.read()
        self.assertIn("tools.deployment config-dir", src)
        self.assertNotIn("git status --porcelain config/$(TENANT) imports/$(TENANT)", src)


if __name__ == "__main__":
    unittest.main()
