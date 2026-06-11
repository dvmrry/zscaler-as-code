"""Round-trip test for the saved-plan chain (make plan SAVE=1 -> make apply).

Drives the REAL Makefile recipes with a fake terraform binary (the TF
variable is the injection seam), pinning the safety contract end to end:
SAVE writes the artifact, apply consumes exactly the artifacts and
deletes them, the destroy guard refuses without ALLOW_DESTROY=1, and an
artifact-less apply fails loudly. Uses a throwaway tenant under envs/
and config/, removed via addCleanup.

Skipped where make/sh are unavailable (e.g. the bare 3.6 floor image) —
the chain itself is shell, not Python.
"""
import os
import shutil
import subprocess
import unittest

FAKE_TF = os.path.join("tools", "tests", "bin", "fake-tf")
TENANT = "tmpchaintest"


def _run(args, extra_env=None):
    env = dict(os.environ)
    env.update(extra_env or {})
    proc = subprocess.run(
        args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env
    )
    return proc.returncode, proc.stdout.decode()


@unittest.skipUnless(shutil.which("make"), "make not available")
class ApplyChainTest(unittest.TestCase):
    def setUp(self):
        self.root = os.path.join("envs", TENANT, "fake_rt")
        self.config_dir = os.path.join("config", TENANT)
        os.makedirs(self.root, exist_ok=True)
        os.makedirs(self.config_dir, exist_ok=True)
        self.addCleanup(shutil.rmtree, os.path.join("envs", TENANT), True)
        self.addCleanup(shutil.rmtree, self.config_dir, True)
        with open(os.path.join(self.root, "main.tf"), "w") as f:
            f.write("# fake root for chain test\n")
        with open(
            os.path.join(self.config_dir, "fake_rt.auto.tfvars.json"), "w"
        ) as f:
            f.write('{"items": {}}\n')
        self.tfplan = os.path.join(self.root, "tfplan")

    def _plan_save(self):
        rc, out = _run(
            ["make", "plan", "TENANT=" + TENANT, "SAVE=1", "TF=" + FAKE_TF]
        )
        self.assertEqual(rc, 0, out)

    def test_plan_save_writes_artifact(self):
        self._plan_save()
        self.assertTrue(os.path.exists(self.tfplan))

    def test_apply_consumes_artifact_and_deletes_it(self):
        self._plan_save()
        log = os.path.join(self.config_dir, "applied.log")
        rc, out = _run(
            ["make", "apply", "TENANT=" + TENANT, "TF=" + FAKE_TF],
            {"FAKE_TF_LOG": log},
        )
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(self.tfplan), "tfplan not deleted")
        with open(log) as f:
            self.assertIn("fake_rt", f.read())

    def test_apply_refuses_destroys_without_allow(self):
        self._plan_save()
        rc, out = _run(
            ["make", "apply", "TENANT=" + TENANT, "TF=" + FAKE_TF],
            {"FAKE_TF_DESTROYS": "2"},
        )
        self.assertNotEqual(rc, 0, "destroy plan applied without ALLOW_DESTROY")
        self.assertIn("ALLOW_DESTROY", out)
        # artifact survives so a human can inspect exactly what was refused
        self.assertTrue(os.path.exists(self.tfplan))

    def test_apply_destroys_proceed_with_allow(self):
        self._plan_save()
        rc, out = _run(
            ["make", "apply", "TENANT=" + TENANT, "TF=" + FAKE_TF,
             "ALLOW_DESTROY=1"],
            {"FAKE_TF_DESTROYS": "2"},
        )
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(self.tfplan))

    def test_apply_without_artifacts_fails_loudly(self):
        rc, out = _run(["make", "apply", "TENANT=" + TENANT, "TF=" + FAKE_TF])
        self.assertNotEqual(rc, 0)
        self.assertIn("no saved plans", out)


if __name__ == "__main__":
    unittest.main()
