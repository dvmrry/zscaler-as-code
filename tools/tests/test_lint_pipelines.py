"""Tests for tools/lint_pipelines.py — cross-pipeline consistency lint.

Each rule is grounded in a real incident; each test pins one. The headline
guard is the dogfood test at the bottom: the committed pipelines/ examples
must lint clean, so the linter and the examples can never silently disagree.
Stdlib-only, offline, deterministic.
"""
import os
import shutil
import tempfile
import unittest

from tools.lint_pipelines import (
    Report,
    _split_key_value,
    backend_profile,
    check_auth_routing,
    check_backend_strategy,
    check_config_in_env,
    check_terraform_version,
    check_tenant_compile_time,
    classify,
    collect_versions,
    config_kind,
    lint_dir,
    main,
    parse,
)


def report():
    return Report()


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class SplitKeyValueTest(unittest.TestCase):
    def test_plain_mapping(self):
        self.assertEqual(_split_key_value("tenant: zs2"), ("tenant", "zs2"))

    def test_colon_only_ends_line(self):
        self.assertEqual(_split_key_value("env:"), ("env", ""))

    def test_colon_inside_template_expr_does_not_split(self):
        k, v = _split_key_value("${{ if eq(p.naming, 'x') }}: $(FOO)")
        self.assertEqual(k, "${{ if eq(p.naming, 'x') }}")
        self.assertEqual(v, "$(FOO)")

    def test_seq_scalar_has_no_mapping_colon(self):
        self.assertEqual(_split_key_value("ZIA_PASSWORD"), (None, None))


class ParseStructureTest(unittest.TestCase):
    def test_env_entry_parent_is_env(self):
        lines = parse("steps:\n"
                      "  - script: echo hi\n"
                      "    env:\n"
                      "      ZIA_CLOUD: $(ZS2_ZIA_CLOUD)\n")
        entry = [l for l in lines if l.key == "ZIA_CLOUD"][0]
        self.assertEqual(entry.parent_key, "env")

    def test_block_scalar_body_owner_is_script(self):
        lines = parse("steps:\n"
                      "  - script: |\n"
                      "      make plan TENANT=zs2\n")
        body = [l for l in lines if "make plan" in l.raw][0]
        self.assertEqual(body.kind, "scalar")
        self.assertEqual(body.block_owner, "script")

    def test_command_block_owner_is_command_not_script(self):
        lines = parse("steps:\n"
                      "  - template: steps/zscaler-auth.yml\n"
                      "    parameters:\n"
                      "      command: |\n"
                      "        make plan TENANT=zs2\n")
        body = [l for l in lines if "make plan" in l.raw][0]
        self.assertEqual(body.block_owner, "command")

    def test_comment_lines_are_kind_comment(self):
        lines = parse("# HTTPS_PROXY: $(HTTPS_PROXY)\nfoo: bar\n")
        self.assertEqual(lines[0].kind, "comment")

    def test_block_scalar_ends_at_shallower_line(self):
        lines = parse("steps:\n"
                      "  - script: |\n"
                      "      make plan\n"
                      "    displayName: Plan\n")
        disp = [l for l in lines if l.key == "displayName"][0]
        self.assertEqual(disp.kind, "mapping")

    def test_block_scalar_indicator_with_trailing_comment(self):
        lines = parse("steps:\n  - script: |  # run it\n      make plan\n")
        body = [l for l in lines if "make plan" in l.raw][0]
        self.assertEqual(body.kind, "scalar")
        self.assertEqual(body.block_owner, "script")


# ---------------------------------------------------------------------------
# R1 — terraform version consistency
# ---------------------------------------------------------------------------

class TerraformVersionTest(unittest.TestCase):
    def test_identical_versions_pass(self):
        a = parse("steps:\n  - script: make install-tf VERSION=1.15.4\n")
        b = parse('  terraform_version: "1.15.4"\n')
        r = report()
        check_terraform_version(collect_versions([("a", a), ("b", b)]), r)
        self.assertEqual(r.errors, [])

    def test_divergent_versions_error(self):
        a = parse("  - script: make install-tf VERSION=1.15.4\n")
        b = parse('      terraform_version: "1.15.7"\n')
        r = report()
        check_terraform_version(collect_versions([("a", a), ("b", b)]), r)
        self.assertTrue(any("DIFFERENT terraform versions" in e
                            for e in r.errors))

    def test_commented_version_is_ignored(self):
        a = parse('  # terraformVersion: "1.99.9"\n'
                  "  - script: make install-tf VERSION=1.15.4\n")
        r = report()
        check_terraform_version(collect_versions([("a", a)]), r)
        self.assertEqual(r.errors, [])

    def test_expected_pin_flags_mismatch(self):
        a = parse("  - script: make install-tf VERSION=1.15.4\n")
        r = report()
        check_terraform_version(collect_versions([("a", a)]), r,
                                expected="1.16.0")
        self.assertTrue(any("!= expected 1.16.0" in e for e in r.errors))

    def test_installtf_param_version_is_counted(self):
        # the job-setup.yml `installTf:` knob is a real version pin; two
        # pipelines pinning different installTf values must read as drift.
        a = parse("    - template: steps/job-setup.yml\n"
                  "      parameters: { installTf: 1.15.4 }\n")
        b = parse("    - template: steps/job-setup.yml\n"
                  "      parameters: { installTf: 1.16.0 }\n")
        r = report()
        check_terraform_version(collect_versions([("a", a), ("b", b)]), r)
        self.assertTrue(any("DIFFERENT terraform versions" in e
                            for e in r.errors))

    def test_version_in_trailing_comment_is_ignored(self):
        # a version mentioned in a trailing comment is not a live pin
        a = parse("  - uses: x  # was: terraform_version: 1.99.9\n"
                  "  - script: make install-tf VERSION=1.15.4\n")
        r = report()
        check_terraform_version(collect_versions([("a", a)]), r)
        self.assertEqual(r.errors, [])


# ---------------------------------------------------------------------------
# R2 — auth routing
# ---------------------------------------------------------------------------

class AuthRoutingTest(unittest.TestCase):
    def test_inline_cred_env_is_error(self):
        lines = parse("steps:\n"
                      "  - script: |\n"
                      '      eval "$(python3 -m tools.cred_env zs2)"\n')
        r = report()
        check_auth_routing("p", lines, r)
        self.assertTrue(any("cred_env invoked inline" in e for e in r.errors))

    def test_creds_make_in_bare_script_is_error(self):
        lines = parse("steps:\n"
                      "  - script: |\n"
                      "      make plan TENANT=zs2\n")
        r = report()
        check_auth_routing("p", lines, r)
        self.assertTrue(any("bare script step" in e for e in r.errors))

    def test_creds_make_in_command_is_clean(self):
        lines = parse("steps:\n"
                      "  - template: steps/zscaler-auth.yml\n"
                      "    parameters:\n"
                      "      command: |\n"
                      "        make plan TENANT=zs2\n")
        r = report()
        check_auth_routing("p", lines, r)
        self.assertEqual(r.errors, [])

    def test_non_creds_make_in_script_is_clean(self):
        lines = parse("steps:\n"
                      "  - script: |\n"
                      "      make assert-clean\n"
                      "      make clean-plans\n")
        r = report()
        check_auth_routing("p", lines, r)
        self.assertEqual(r.errors, [])

    def test_compound_targets_are_not_creds_make(self):
        # `plan` must not fire on plan-report/plan-checks, `drift` not on
        # drift-report, `fetch` not on fetch-diag — none need credentials.
        for t in ("plan-report", "plan-checks", "drift-report", "fetch-diag"):
            lines = parse("steps:\n  - script: |\n      make %s TENANT=z\n" % t)
            r = report()
            check_auth_routing("p", lines, r)
            self.assertEqual(r.errors, [], t)

    def test_make_with_flag_argument_still_caught(self):
        lines = parse("steps:\n  - script: |\n      make -C . fetch TENANT=z\n")
        r = report()
        check_auth_routing("p", lines, r)
        self.assertTrue(any("bare script step" in e for e in r.errors))

    def test_creds_make_in_bash_block_is_error(self):
        lines = parse("steps:\n  - bash: |\n      make plan TENANT=z\n")
        r = report()
        check_auth_routing("p", lines, r)
        self.assertTrue(any("bare script step" in e for e in r.errors))

    def test_creds_make_in_command_with_trailing_comment_indicator(self):
        # `command: |  # note` must still be recognized as a block scalar, so
        # the make inside it is a command (routed), not a bare script.
        lines = parse("steps:\n"
                      "  - template: steps/zscaler-auth.yml\n"
                      "    parameters:\n"
                      "      command: |  # the plan\n"
                      "        make plan TENANT=z\n")
        r = report()
        check_auth_routing("p", lines, r)
        self.assertEqual(r.errors, [])


# ---------------------------------------------------------------------------
# R3 — non-secret config in env
# ---------------------------------------------------------------------------

class ConfigKindTest(unittest.TestCase):
    def test_cloud(self):
        self.assertEqual(config_kind("ZIA_CLOUD"), "cloud")
        self.assertEqual(config_kind("ZS2_ZPA_CLOUD"), "cloud")

    def test_legacy_flag(self):
        self.assertEqual(config_kind("ZSCALER_USE_LEGACY_CLIENT"),
                         "legacy-mode flag")

    def test_proxy(self):
        self.assertEqual(config_kind("HTTPS_PROXY"), "proxy")
        self.assertEqual(config_kind("no_proxy"), "proxy")

    def test_customer_id(self):
        self.assertEqual(config_kind("ZPA_CUSTOMER_ID"), "ZPA customer id")

    def test_secrets_are_not_config(self):
        for secret in ("ZS2_ZIA_PASSWORD", "ZIA_API_KEY",
                       "ZSCALER_CLIENT_SECRET", "ARM_SAS_TOKEN",
                       "ZSCALER_CLIENT_ID"):
            self.assertIsNone(config_kind(secret), secret)

    def test_template_directive_key_is_not_config(self):
        self.assertIsNone(config_kind("${{ each s in parameters.secrets }}"))

    def test_customer_id_only_matches_zpa(self):
        self.assertIsNone(config_kind("MY_VENDOR_CUSTOMER_ID"))
        self.assertEqual(config_kind("ZS2_ZPA_CUSTOMER_ID"), "ZPA customer id")


class ConfigInEnvTest(unittest.TestCase):
    def test_cloud_in_env_is_error(self):
        lines = parse("steps:\n"
                      "  - script: echo\n"
                      "    env:\n"
                      "      ZIA_CLOUD: $(ZS2_ZIA_CLOUD)\n")
        r = report()
        check_config_in_env("p", lines, r)
        self.assertTrue(any("cloud 'ZIA_CLOUD'" in e for e in r.errors))

    def test_secret_in_env_is_clean(self):
        lines = parse("steps:\n"
                      "  - script: echo\n"
                      "    env:\n"
                      "      ZS2_ZIA_PASSWORD: $(ZS2_ZIA_PASSWORD)\n")
        r = report()
        check_config_in_env("p", lines, r)
        self.assertEqual(r.errors, [])

    def test_config_outside_env_block_is_clean(self):
        # a `command:` whose text mentions a cloud is not an env mapping
        lines = parse("steps:\n"
                      "  - script: |\n"
                      "      echo ZIA_CLOUD is set\n")
        r = report()
        check_config_in_env("p", lines, r)
        self.assertEqual(r.errors, [])


# ---------------------------------------------------------------------------
# R5 — tenant compile-time
# ---------------------------------------------------------------------------

class TenantCompileTimeTest(unittest.TestCase):
    def test_runtime_macro_tenant_is_error(self):
        lines = parse("    parameters:\n      tenant: $(TENANT)\n")
        r = report()
        check_tenant_compile_time("p", lines, r)
        self.assertTrue(any("runtime macro" in e for e in r.errors))

    def test_compile_time_tenant_is_clean(self):
        lines = parse("    parameters:\n"
                      "      tenant: ${{ variables.TENANT }}\n")
        r = report()
        check_tenant_compile_time("p", lines, r)
        self.assertEqual(r.errors, [])

    def test_quoted_runtime_macro_tenant_is_error(self):
        for val in ("'$(TENANT)'", '"$(TENANT)"'):
            lines = parse("    parameters:\n      tenant: %s\n" % val)
            r = report()
            check_tenant_compile_time("p", lines, r)
            self.assertTrue(any("runtime macro" in e for e in r.errors), val)


# ---------------------------------------------------------------------------
# R4 — backend.conf strategy (cross-file)
# ---------------------------------------------------------------------------

class BackendStrategyTest(unittest.TestCase):
    def test_inline_materialization_detected(self):
        lines = parse("steps:\n"
                      "  - script: |\n"
                      "      printf 'x = 1\\n' > backend.conf\n")
        references, materializes = backend_profile(lines)
        self.assertTrue(references)
        self.assertTrue(materializes)

    def test_split_strategy_warns(self):
        inline = parse("  - script: |\n      printf 'x' > backend.conf\n")
        committed = parse("  - script: make plan BACKEND_CONFIG=backend.conf\n")
        r = report()
        check_backend_strategy([("a", inline), ("b", committed)], r)
        self.assertTrue(any("split across pipelines" in w for w in r.warnings))

    def test_uniform_strategy_no_warn(self):
        a = parse("  - script: make plan BACKEND_CONFIG=backend.conf\n")
        b = parse("  - script: make apply BACKEND_CONFIG=backend.conf\n")
        r = report()
        check_backend_strategy([("a", a), ("b", b)], r)
        self.assertEqual(r.warnings, [])


# ---------------------------------------------------------------------------
# classify + driver
# ---------------------------------------------------------------------------

class ClassifyTest(unittest.TestCase):
    def test_step_template(self):
        self.assertEqual(
            classify("pipelines/steps/zscaler-auth.yml", ""), "template")

    def test_ado_by_name(self):
        self.assertEqual(
            classify("pipelines/azure-pipelines-drift.example.yml", ""), "ado")

    def test_gha_by_content(self):
        self.assertEqual(classify("pipelines/ci.yml", "runs-on: ubuntu\n"),
                         "gha")


class MainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, text):
        with open(os.path.join(self.tmp, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_empty_dir_is_not_success(self):
        self.assertEqual(main(["--dir", self.tmp]), 2)

    def test_nonexistent_dir_errors(self):
        self.assertEqual(main(["--dir", self.tmp + "/nope"]), 2)

    def test_clean_dir_returns_zero(self):
        self._write("azure-pipelines-x.yml",
                    "steps:\n"
                    "  - template: steps/zscaler-auth.yml\n"
                    "    parameters:\n"
                    "      tenant: ${{ variables.TENANT }}\n"
                    "      command: make plan TENANT=zs2\n")
        self.assertEqual(main(["--dir", self.tmp]), 0)

    def test_drift_dir_returns_one(self):
        self._write("azure-pipelines-x.yml",
                    "steps:\n  - script: make install-tf VERSION=1.15.4\n")
        self._write("azure-pipelines-y.yml",
                    "steps:\n  - script: make install-tf VERSION=1.16.0\n")
        self.assertEqual(main(["--dir", self.tmp]), 1)

    def test_explicit_files_ignore_other_yaml_in_dir(self):
        # pipelines sharing a dir with an example: naming only the operative
        # files keeps the example out of the cross-file version comparison.
        self._write("azure-pipelines-real.yml",
                    "steps:\n  - script: make install-tf VERSION=1.16.0\n")
        self._write("azure-pipelines-example.yml",
                    "steps:\n  - script: make install-tf VERSION=1.15.4\n")
        real = os.path.join(self.tmp, "azure-pipelines-real.yml")
        # linting only the real file: no drift (the example is not compared)
        self.assertEqual(main([real]), 0)
        # the whole dir: the example drags in a different version -> drift
        self.assertEqual(main(["--dir", self.tmp]), 1)

    def test_missing_explicit_file_errors(self):
        self.assertEqual(main([os.path.join(self.tmp, "nope.yml")]), 2)


class DogfoodTest(unittest.TestCase):
    """The committed examples must lint clean — linter and examples can't
    silently disagree (AGENTS.md rule 8: behavior pinned by a fixture)."""

    def test_repo_examples_are_consistent(self):
        here = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        pipelines = os.path.join(here, "pipelines")
        report, count = lint_dir(pipelines)
        self.assertGreater(count, 0)
        self.assertEqual(report.errors, [],
                         "committed examples must lint clean:\n%s"
                         % "\n".join(report.errors))
        self.assertEqual(report.warnings, [],
                         "committed examples must produce no warnings:\n%s"
                         % "\n".join(report.warnings))


if __name__ == "__main__":
    unittest.main()
