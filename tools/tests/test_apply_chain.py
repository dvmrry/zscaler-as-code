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
        # Register cleanup BEFORE creating anything, so a raise in the
        # second makedirs still tears down what the first one created.
        self.addCleanup(shutil.rmtree, os.path.join("envs", TENANT), True)
        self.addCleanup(shutil.rmtree, self.config_dir, True)
        os.makedirs(self.root, exist_ok=True)
        os.makedirs(self.config_dir, exist_ok=True)
        with open(os.path.join(self.root, "main.tf"), "w", encoding="utf-8") as f:
            f.write("# fake root for chain test\n")
        with open(
            os.path.join(self.config_dir, "fake_rt.auto.tfvars.json"), "w"
        , encoding="utf-8") as f:
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
        with open(log, encoding="utf-8") as f:
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

    def test_apply_refuses_remote_backend_without_backend_config(self):
        # Parity with `make plan`: a saved plan whose root declares a remote
        # backend must refuse without BACKEND_CONFIG and emit the repo's
        # remediation, not a bare terraform backend error. The artifact must
        # survive so a human can re-run with BACKEND_CONFIG.
        self._plan_save()
        with open(os.path.join(self.root, "main.tf"), "w", encoding="utf-8") as f:
            f.write('terraform {\n  backend "azurerm" {}\n}\n')
        rc, out = _run(
            ["make", "apply", "TENANT=" + TENANT, "TF=" + FAKE_TF])
        self.assertNotEqual(rc, 0, "apply ran a remote-backend root with no BACKEND_CONFIG")
        self.assertIn("BACKEND_CONFIG", out)
        self.assertTrue(os.path.exists(self.tfplan))

    def test_assert_clean_passes_on_noop_and_import_only_plans(self):
        self._plan_save()
        rc, out = _run(
            ["make", "assert-clean", "TENANT=" + TENANT, "TF=" + FAKE_TF])
        self.assertEqual(rc, 0, out)
        self.assertIn("clean", out)

    def test_assert_clean_fails_on_real_changes(self):
        # the drift-PR auto-merge gate: tenant moved between fetch and plan
        self._plan_save()
        rc, out = _run(
            ["make", "assert-clean", "TENANT=" + TENANT, "TF=" + FAKE_TF],
            {"FAKE_TF_UPDATES": "1"},
        )
        self.assertNotEqual(rc, 0)
        self.assertIn("NOT CLEAN", out)

    def test_plan_report_renders_markdown(self):
        self._plan_save()
        self.addCleanup(lambda: os.path.exists("reports/plan.md") and os.remove("reports/plan.md"))
        rc, out = _run(
            ["make", "plan-report", "TENANT=" + TENANT, "TF=" + FAKE_TF])
        self.assertEqual(rc, 0, out)
        with open(os.path.join("reports", "plan.md"), encoding="utf-8") as f:
            body = f.read()
        self.assertIn("fake_rt", body)
        self.assertIn("Plan:", body)
        self.assertIn("```", body)

    def test_plan_report_without_plans_fails_loudly(self):
        rc, out = _run(
            ["make", "plan-report", "TENANT=" + TENANT, "TF=" + FAKE_TF])
        self.assertNotEqual(rc, 0)
        self.assertIn("no saved plans", out)

    def test_stage_imports_roundtrip(self):
        os.makedirs(os.path.join("imports", TENANT), exist_ok=True)
        self.addCleanup(shutil.rmtree, os.path.join("imports", TENANT), True)
        with open(os.path.join("imports", TENANT, "fake_rt_imports.tf"),
                  "w", encoding="utf-8") as f:
            f.write('import {\n  to = module.fake_rt.fake_rt.this["a"]\n  id = "1"\n}\n')
        with open(os.path.join("imports", TENANT, "fake_rt_moves.tf"),
                  "w", encoding="utf-8") as f:
            f.write('moved {\n  from = module.fake_rt.fake_rt.this["a"]\n  to   = module.fake_rt.fake_rt.this["b"]\n}\n')
        rc, out = _run(["make", "stage-imports", "TENANT=" + TENANT])
        self.assertEqual(rc, 0, out)
        self.assertTrue(os.path.exists(os.path.join(self.root, "fake_rt_imports.tf")))
        self.assertTrue(os.path.exists(os.path.join(self.root, "fake_rt_moves.tf")))
        rc, out = _run(["make", "unstage-imports", "TENANT=" + TENANT])
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(os.path.join(self.root, "fake_rt_imports.tf")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "fake_rt_moves.tf")))

    def test_stage_imports_nothing_to_stage_fails_loudly(self):
        rc, out = _run(["make", "stage-imports", "TENANT=" + TENANT])
        self.assertNotEqual(rc, 0)
        self.assertIn("nothing to stage", out)

    def test_assert_clean_without_plans_fails_loudly(self):
        rc, out = _run(
            ["make", "assert-clean", "TENANT=" + TENANT, "TF=" + FAKE_TF])
        self.assertNotEqual(rc, 0)
        self.assertIn("no saved plans", out)

    def test_apply_aborts_when_show_lacks_resource_changes(self):
        # The destroy-guard must not read parseable-but-wrong show JSON (no
        # resource_changes) as "0 destroys" and proceed. The helper exits 1
        # on a missing resource_changes, and set -e aborts before apply.
        self._plan_save()
        rc, out = _run(
            ["make", "apply", "TENANT=" + TENANT, "TF=" + FAKE_TF],
            {"FAKE_TF_NORC": "1"},
        )
        self.assertNotEqual(rc, 0, "apply proceeded on show output with no resource_changes")
        # the saved plan must survive — apply never reached the apply step
        self.assertTrue(os.path.exists(self.tfplan))

    def test_assert_clean_aborts_when_show_lacks_resource_changes(self):
        # The merge-readiness gate must not declare a plan clean off show
        # output that lacks resource_changes; the helper exits 1 instead.
        self._plan_save()
        rc, out = _run(
            ["make", "assert-clean", "TENANT=" + TENANT, "TF=" + FAKE_TF],
            {"FAKE_TF_NORC": "1"},
        )
        self.assertNotEqual(rc, 0, "assert-clean reported clean on show output with no resource_changes")


if __name__ == "__main__":
    unittest.main()
