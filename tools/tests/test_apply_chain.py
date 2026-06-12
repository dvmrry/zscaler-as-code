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
    # The apply branch guard reads CI ref vars before git; tests run on
    # dev branches, so simulate main unless a test overrides it.
    env.setdefault("BUILD_SOURCEBRANCH", "refs/heads/main")
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

    def test_apply_refused_off_main(self):
        self._plan_save()
        rc, out = _run(
            ["make", "apply", "TENANT=" + TENANT, "TF=" + FAKE_TF],
            {"BUILD_SOURCEBRANCH": "refs/heads/feature-x"},
        )
        self.assertNotEqual(rc, 0)
        self.assertIn("apply refused", out)
        # the saved plan survives the refusal for inspection
        self.assertTrue(os.path.exists(self.tfplan))

    def test_apply_branch_override_and_custom_main(self):
        self._plan_save()
        rc, out = _run(
            ["make", "apply", "TENANT=" + TENANT, "TF=" + FAKE_TF,
             "ALLOW_NON_MAIN=1"],
            {"BUILD_SOURCEBRANCH": "refs/heads/feature-x"},
        )
        self.assertEqual(rc, 0, out)
        # custom default branch honored
        self._plan_save()
        rc, out = _run(
            ["make", "apply", "TENANT=" + TENANT, "TF=" + FAKE_TF,
             "MAIN_BRANCH=trunk"],
            {"BUILD_SOURCEBRANCH": "refs/heads/trunk"},
        )
        self.assertEqual(rc, 0, out)

    def test_stale_plans_cleared_before_new_plan_set(self):
        # The field scenario: full bootstrap plan -> cancel -> scoped
        # re-run on a REUSED agent workspace. Stale tfplans must not ride
        # into the next apply: clean-plans removes them.
        stale_root = os.path.join("envs", TENANT, "stale_rt")
        os.makedirs(stale_root, exist_ok=True)
        with open(os.path.join(stale_root, "main.tf"), "w",
                  encoding="utf-8") as f:
            f.write("# stale root\n")
        with open(os.path.join(stale_root, "tfplan"), "w",
                  encoding="utf-8") as f:
            f.write("stale\n")
        rc, out = _run(["make", "clean-plans", "TENANT=" + TENANT])
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(os.path.join(stale_root, "tfplan")))
        self.assertIn("1 stale plan(s) removed", out)

    def test_plan_changed_recipe_cleans_first(self):
        # plan-changed defines the run's plan set: its recipe must invoke
        # clean-plans before planning (dry-run inspection).
        rc, out = _run(["make", "-n", "plan-changed", "BASE=HEAD"])
        self.assertEqual(rc, 0, out)
        self.assertIn("clean-plans", out)

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

    def test_stage_imports_state_aware_keeps_only_delta(self):
        # Bootstrap RE-RUN semantics: terraform errors on importing an
        # already-managed address, so state-aware staging filters those
        # blocks out — re-runs adopt only the delta.
        os.makedirs(os.path.join("imports", TENANT), exist_ok=True)
        self.addCleanup(shutil.rmtree, os.path.join("imports", TENANT), True)
        with open(os.path.join("imports", TENANT, "fake_rt_imports.tf"),
                  "w", encoding="utf-8") as f:
            f.write(
                'import {\n  to = module.fake_rt.fake_rt.this["managed"]\n  id = "1"\n}\n\n'
                'import {\n  to = module.fake_rt.fake_rt.this["brand_new"]\n  id = "2"\n}\n'
            )
        rc, out = _run(
            ["make", "stage-imports", "TENANT=" + TENANT, "STATE_AWARE=1",
             "TF=" + FAKE_TF],
            {"FAKE_TF_STATE": 'module.fake_rt.fake_rt.this["managed"]'},
        )
        self.assertEqual(rc, 0, out)
        with open(os.path.join(self.root, "fake_rt_imports.tf"),
                  encoding="utf-8") as f:
            staged = f.read()
        self.assertIn("brand_new", staged)
        self.assertNotIn('"managed"', staged)

    def test_stage_imports_state_aware_empty_delta_is_noop(self):
        os.makedirs(os.path.join("imports", TENANT), exist_ok=True)
        self.addCleanup(shutil.rmtree, os.path.join("imports", TENANT), True)
        with open(os.path.join("imports", TENANT, "fake_rt_imports.tf"),
                  "w", encoding="utf-8") as f:
            f.write('import {\n  to = module.fake_rt.fake_rt.this["managed"]\n  id = "1"\n}\n')
        rc, out = _run(
            ["make", "stage-imports", "TENANT=" + TENANT, "STATE_AWARE=1",
             "TF=" + FAKE_TF],
            {"FAKE_TF_STATE": 'module.fake_rt.fake_rt.this["managed"]'},
        )
        self.assertEqual(rc, 0, out)
        self.assertIn("delta is empty", out)
        self.assertFalse(
            os.path.exists(os.path.join(self.root, "fake_rt_imports.tf")))

    def test_product_token_scopes_glob_targets(self):
        # RESOURCE=zia must expand to zia_* on the per-root targets
        # (field report: bare product tokens previously matched nothing).
        zia_root = os.path.join("envs", TENANT, "zia_fake")
        os.makedirs(zia_root, exist_ok=True)
        with open(os.path.join(zia_root, "main.tf"), "w", encoding="utf-8") as f:
            f.write("# zia fake root\n")
        with open(os.path.join(self.config_dir, "zia_fake.auto.tfvars.json"),
                  "w", encoding="utf-8") as f:
            f.write('{"items": {}}\n')
        rc, out = _run(
            ["make", "plan", "TENANT=" + TENANT, "RESOURCE=zia", "SAVE=1",
             "TF=" + FAKE_TF])
        self.assertEqual(rc, 0, out)
        self.assertTrue(
            os.path.exists(os.path.join(zia_root, "tfplan")))
        # the non-zia root was NOT planned
        self.assertFalse(os.path.exists(self.tfplan))

    def test_make_clean_removes_run_artifacts_only(self):
        self._plan_save()
        staged = os.path.join(self.root, "fake_rt_imports.tf")
        with open(staged, "w", encoding="utf-8") as f:
            f.write("import {}\n")
        os.makedirs("reports", exist_ok=True)
        with open(os.path.join("reports", "plan.md"), "w", encoding="utf-8") as f:
            f.write("x\n")
        rc, out = _run(["make", "clean"])
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(self.tfplan))
        self.assertFalse(os.path.exists(staged))
        self.assertFalse(os.path.exists("reports"))
        # committed/source files untouched
        self.assertTrue(os.path.exists(os.path.join(self.root, "main.tf")))

    def test_unlock_passes_lock_id_to_force_unlock(self):
        log = os.path.join(self.config_dir, "unlock.log")
        rc, out = _run(
            ["make", "unlock", "TENANT=" + TENANT, "RESOURCE=fake_rt",
             "LOCK_ID=abc-123", "TF=" + FAKE_TF],
            {"FAKE_TF_LOG": log},
        )
        self.assertEqual(rc, 0, out)
        self.assertIn("CAUTION", out)
        with open(log, encoding="utf-8") as f:
            self.assertIn("force-unlock -force abc-123", f.read())

    def test_forget_runs_state_rm_on_the_exact_address(self):
        log = os.path.join(self.config_dir, "forget.log")
        rc, out = _run(
            ["make", "forget", "TENANT=" + TENANT, "RESOURCE=fake_rt",
             "KEY=office_365_one_click", "TF=" + FAKE_TF],
            {"FAKE_TF_LOG": log},
        )
        self.assertEqual(rc, 0, out)
        self.assertIn("still exists in the tenant", out)
        with open(log, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("state rm", content)
        self.assertIn(
            'module.fake_rt.fake_rt.this["office_365_one_click"]', content)

    def test_unlock_requires_all_args(self):
        rc, out = _run(["make", "unlock", "TENANT=" + TENANT, "TF=" + FAKE_TF])
        self.assertNotEqual(rc, 0)
        self.assertIn("LOCK_ID", out)

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
