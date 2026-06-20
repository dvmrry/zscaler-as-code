import os
import shutil
import subprocess
import unittest


def _make(*targets):
    return subprocess.run(
        ["make", "--no-print-directory", "-f", "Makefile"] + list(targets),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )


@unittest.skipUnless(shutil.which("make"), "make not available")
class SavePlansRoundTripTest(unittest.TestCase):
    """save-plans/restore-plans own the tfplan archive path: a saved plan must
    survive save -> (delete in place) -> restore, landing back in the resolved
    env root where `apply` reads it. Drives the real make targets against dummy
    tfplan files in a template (demo) env root — no terraform/provider needed.
    Both reports/ and envs/**/tfplan are gitignored, so this never dirties the
    tracked tree."""

    RT = "zia_url_filtering_rules"  # a real demo env root

    def setUp(self):
        self.env_root = os.path.join("envs", "demo", self.RT)
        self.assertTrue(os.path.isdir(self.env_root),
                        "fixture env root missing: %s" % self.env_root)
        self.plan = os.path.join(self.env_root, "tfplan")
        self.archive = os.path.join("reports", "tfplans", "demo", self.RT, "tfplan")
        self._clean()
        with open(self.plan, "wb") as f:
            f.write(b"DUMMY-TFPLAN-BYTES")

    def tearDown(self):
        self._clean()

    def _clean(self):
        for p in (self.plan, self.archive):
            if os.path.exists(p):
                os.remove(p)
        shutil.rmtree(os.path.join("reports", "tfplans"), ignore_errors=True)

    def test_round_trip_preserves_plan(self):
        # save: env-root tfplan -> reports/tfplans/<t>/<rt>/tfplan
        r = _make("save-plans")
        self.assertEqual(r.returncode, 0, r.stdout.decode())
        self.assertTrue(os.path.isfile(self.archive),
                        "save-plans did not archive the plan: %s\n%s"
                        % (self.archive, r.stdout.decode()))

        # delete the in-place plan (stands in for clean-plans)
        os.remove(self.plan)
        self.assertFalse(os.path.exists(self.plan))

        # restore: archive -> resolved env root (where apply reads it)
        r = _make("restore-plans")
        self.assertEqual(r.returncode, 0, r.stdout.decode())
        self.assertTrue(os.path.isfile(self.plan),
                        "restore-plans did not return the plan to %s\n%s"
                        % (self.plan, r.stdout.decode()))
        with open(self.plan, "rb") as f:
            self.assertEqual(f.read(), b"DUMMY-TFPLAN-BYTES")


if __name__ == "__main__":
    unittest.main()
