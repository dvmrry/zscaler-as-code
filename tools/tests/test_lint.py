"""Tests for tools/lint.py — semantic misconfig detection.

The headline case: ZIA most-specific-match shadowing. A URL added to
one category that is more specific than an entry in another category
silently pulls that traffic out of the policies matching the broader
entry (the SSL-bypass scenario). All checks offline and deterministic.
"""
import io
import os
import shutil
import sys
import unittest

from tools.lint import (
    Report,
    _shadows,
    _split_entry,
    check_category_shadowing,
    check_ip_entry,
    check_list_duplicates,
    check_order_collisions,
    check_ranges,
    check_string,
    check_url_entry,
)
from tools.transform import render_tfvars


def report():
    return Report()


class StringHygieneTest(unittest.TestCase):
    def test_clean_string_passes(self):
        r = report()
        check_string("Allow Engineering", "rt", "w", r)
        self.assertEqual(r.errors + r.warnings, [])

    def test_zero_width_is_error(self):
        r = report()
        check_string("exam​ple.com", "rt", "w", r)
        self.assertTrue(any("invisible" in e for e in r.errors))

    def test_control_char_is_error(self):
        r = report()
        check_string("name\x0b", "rt", "w", r)
        self.assertTrue(any("control" in e for e in r.errors))

    def test_edge_whitespace_is_error(self):
        r = report()
        check_string(" padded ", "rt", "w", r)
        self.assertTrue(any("whitespace" in e for e in r.errors))

    def test_smart_quote_is_warning_not_error(self):
        r = report()
        check_string("John’s laptop policy", "rt", "w", r)
        self.assertEqual(r.errors, [])
        self.assertTrue(any("pasted punctuation" in w for w in r.warnings))

    def test_html_entity_is_warning(self):
        r = report()
        check_string("R&amp;D rule", "rt", "w", r)
        self.assertEqual(r.errors, [])
        self.assertTrue(any("HTML entity" in w for w in r.warnings))

    def test_bare_ampersand_is_clean(self):
        r = report()
        check_string("R&D rule ?a=1&b=2", "rt", "w", r)
        self.assertEqual(r.errors + r.warnings, [])


class DroppedFieldGateTest(unittest.TestCase):
    """Hand-edits re-adding transform-dropped fields perma-diff (the
    provider rewrites or never returns them) — lint must gate, at both
    nesting levels."""

    def _lint(self, items, override):
        from tools.lint import check_dropped_fields
        r = report()
        check_dropped_fields(items, "zpa_policy_access_rule", override, r)
        return r

    def test_top_level_dropped_field_is_error(self):
        r = self._lint({"k": {"name": "R", "priority": "3"}},
                       {"drops": ["priority"]})
        self.assertTrue(any("priority" in e for e in r.errors))

    def test_nested_dropped_field_is_error(self):
        items = {"k": {"conditions": [{"operands": [
            {"object_type": "APP", "rhs_list": ["a", "b"]}]}]}}
        r = self._lint(items, {"drops": ["conditions.operands.rhs_list"]})
        self.assertEqual(len(r.errors), 1)
        self.assertIn("rhs_list", r.errors[0])
        self.assertIn("items.k.conditions[0].operands[0]", r.errors[0])

    def test_clean_config_passes(self):
        items = {"k": {"conditions": [{"operands": [
            {"object_type": "APP", "rhs": "1"}]}]}}
        r = self._lint(items, {"drops": ["conditions.operands.rhs_list",
                                         "priority"]})
        self.assertEqual(r.errors, [])

    def test_uppercase_domain_names_warned(self):
        from tools.lint import URL_ENTRY_FIELDS, check_url_entry
        self.assertIn("domain_names", URL_ENTRY_FIELDS)
        r = report()
        check_url_entry("App.Example.COM", "zpa_application_segment",
                        "items.k.domain_names[0]", r, field="domain_names")
        self.assertTrue(any("uppercase" in w for w in r.warnings))

    def test_uppercase_zia_url_entries_is_quiet(self):
        # field verdict: tenant-scale noise — ZIA keeps the tenant's
        # casing and it round-trips fine
        from tools.lint import check_url_entry
        r = report()
        check_url_entry("WWW.Example.COM", "zia_url_categories",
                        "items.k.urls[0]", r, field="urls")
        self.assertEqual(r.warnings, [])


class UrlEntryTest(unittest.TestCase):
    def test_plain_domain_passes(self):
        r = report()
        check_url_entry("sub.example.com", "rt", "w", r)
        check_url_entry("example.com/path", "rt", "w", r)
        self.assertEqual(r.errors, [])

    def test_scheme_is_error(self):
        r = report()
        check_url_entry("https://example.com", "rt", "w", r)
        self.assertTrue(any("scheme" in e for e in r.errors))

    def test_csv_paste_is_error(self):
        r = report()
        check_url_entry("a.com, b.com", "rt", "w", r)
        self.assertTrue(any("separator" in e for e in r.errors))

    def test_double_dot_is_error(self):
        r = report()
        check_url_entry("example..com", "rt", "w", r)
        self.assertTrue(any("consecutive dots" in e for e in r.errors))


class IpEntryTest(unittest.TestCase):
    def test_valid_forms_pass(self):
        r = report()
        for v in ("10.0.0.1", "10.0.0.0/24", "10.0.0.1-10.0.0.9", "2001:db8::/32"):
            check_ip_entry(v, "rt", "w", r)
        self.assertEqual(r.errors, [])

    def test_garbage_is_error(self):
        r = report()
        check_ip_entry("10.0.0.999", "rt", "w", r)
        check_ip_entry("example.com", "rt", "w", r)
        self.assertEqual(len(r.errors), 2)


class DuplicatesTest(unittest.TestCase):
    def test_set_duplicate_is_error(self):
        r = report()
        check_list_duplicates(["a", "a"], "rt", "w", r, True, False)
        self.assertTrue(any("perma-drift" in e for e in r.errors))

    def test_pairwise_port_list_duplicate_is_fine(self):
        # ZPA *_port_ranges are pairwise: ['9002','9002'] = range 9002-9002.
        r = report()
        check_list_duplicates(["9002", "9002"], "rt", "w", r, False, False)
        self.assertEqual(r.errors + r.warnings, [])

    def test_url_list_duplicate_is_warning(self):
        r = report()
        check_list_duplicates(["a.com", "a.com"], "rt", "w", r, False, True)
        self.assertEqual(r.errors, [])
        self.assertEqual(len(r.warnings), 1)


class RangesTest(unittest.TestCase):
    OV = {"ranges": {"size_quota": [10, 100000]}}

    def test_in_range_passes(self):
        r = report()
        check_ranges({"size_quota": 100000}, "k", "rt", self.OV, r)
        self.assertEqual(r.errors, [])

    def test_out_of_range_is_error(self):
        r = report()
        check_ranges({"size_quota": 100001}, "k", "rt", self.OV, r)
        self.assertTrue(any("provider-validated range" in e for e in r.errors))

    def test_string_numeric_ranges_checked(self):
        # zpa latitude/longitude are schema STRINGS holding numbers
        ov = {"ranges": {"latitude": [-90, 90]}}
        r = report()
        check_ranges({"latitude": "137.2"}, "k", "rt", ov, r)
        self.assertEqual(len(r.errors), 1)
        r2 = report()
        check_ranges({"latitude": "45.5"}, "k", "rt", ov, r2)
        self.assertEqual(r2.errors, [])

    def test_absent_field_passes(self):
        r = report()
        check_ranges({}, "k", "rt", self.OV, r)
        self.assertEqual(r.errors, [])


class OrderCollisionTest(unittest.TestCase):
    def test_unique_orders_pass(self):
        r = report()
        check_order_collisions(
            {"a": {"order": 1}, "b": {"order": 2}}, "zia_url_filtering_rules", r)
        self.assertEqual(r.errors, [])

    def test_collision_is_error(self):
        r = report()
        check_order_collisions(
            {"a": {"order": 1}, "b": {"order": 1}}, "zia_url_filtering_rules", r)
        self.assertEqual(len(r.errors), 1)

    def test_grouped_resource_collides_per_group_only(self):
        items = {
            "a": {"order": 1, "type": "STREAMING_MEDIA"},
            "b": {"order": 1, "type": "WEBMAIL"},          # different group: fine
            "c": {"order": 1, "type": "STREAMING_MEDIA"},  # collides with a
        }
        r = report()
        check_order_collisions(items, "zia_cloud_app_control_rule", r)
        self.assertEqual(len(r.errors), 1)


class ShadowingTest(unittest.TestCase):
    def test_split_entry(self):
        self.assertEqual(_split_entry(".Example.COM"), ("example.com", ""))
        self.assertEqual(_split_entry("a.com/x/y/"), ("a.com", "x/y"))

    def test_subdomain_shadows_domain(self):
        self.assertTrue(_shadows(("sub.example.com", ""), ("example.com", "")))

    def test_path_shadows_host(self):
        self.assertTrue(_shadows(("example.com", "x"), ("example.com", "")))

    def test_identical_does_not_shadow(self):
        self.assertFalse(_shadows(("example.com", ""), ("example.com", "")))

    def test_unrelated_does_not_shadow(self):
        self.assertFalse(_shadows(("other.com", ""), ("example.com", "")))
        # suffix without a dot boundary is NOT a subdomain
        self.assertFalse(_shadows(("notexample.com", ""), ("example.com", "")))

    def _with_detail(self, items):
        import os
        r = report()
        os.environ["LINT_SHADOWING"] = "1"
        try:
            check_category_shadowing(items, "zia_url_categories", r)
        finally:
            del os.environ["LINT_SHADOWING"]
        return r

    def test_default_is_a_single_summary_pulse(self):
        # field verdict: 497 rollup lines on a dense tenant is still a
        # report when a pulse is wanted — NEW adds are gated by
        # plan-checks where it matters
        items = {
            "broad_a": {"urls": ["dom1.com"]},
            "broad_b": {"urls": ["dom2.com"]},
            "spec": {"urls": ["s.dom1.com", "s.dom2.com"]},
        }
        r = report()
        check_category_shadowing(items, "zia_url_categories", r)
        self.assertEqual(len(r.warnings), 1)
        self.assertIn("2 category pair(s)", r.warnings[0])
        self.assertIn("plan-checks", r.warnings[0])

    def test_ssl_bypass_scenario_is_flagged(self):
        # The field case: example.com sits in an SSL-bypass category; an
        # operator adds sub.example.com to some other category. ZIA
        # most-specific-match now categorizes that traffic differently and
        # the bypass policy silently stops applying to it.
        items = {
            "ssl_bypass_list": {"urls": ["example.com"]},
            "marketing_block": {"urls": ["sub.example.com"]},
        }
        r = self._with_detail(items)
        self.assertEqual(len(r.warnings), 1)
        self.assertIn("sub.example.com", r.warnings[0])
        self.assertIn("ssl_bypass_list", r.warnings[0])

    def test_same_entry_in_two_categories_is_legal(self):
        # exact multi-membership (the demo's mozart.com case): no warning
        items = {
            "cat_a": {"urls": ["mozart.com"]},
            "cat_b": {"urls": ["mozart.com"]},
        }
        r = report()
        check_category_shadowing(items, "zia_url_categories", r)
        self.assertEqual(r.warnings, [])

    def test_same_category_specificity_is_fine(self):
        # more-specific entry in the SAME category changes nothing
        items = {"cat_a": {"urls": ["example.com", "sub.example.com"]}}
        r = report()
        check_category_shadowing(items, "zia_url_categories", r)
        self.assertEqual(r.warnings, [])


class DemoCleanGateTest(unittest.TestCase):
    def test_demo_tenant_lints_with_zero_errors(self):
        from tools.lint import main

        rc = main(["demo"])
        self.assertEqual(rc, 0, "demo tenant must lint clean")


class MainExitCodeTest(unittest.TestCase):
    """Pin the CLI gate contract end-to-end: an error-bearing run must
    return exit 1, not silently downgrade to a warning/clean pass."""

    def test_error_bearing_tenant_exits_1(self):
        from tools.lint import main

        # A throwaway tenant dir under config/ so lint's
        # os.path.join("config", tenant) resolves to it. The file is
        # written in canonical form (no spurious canonical-form error) so
        # the ONLY error is the URL scheme — that one error must gate.
        tenant = "_linttest_tmp_xyzzy"
        config_dir = os.path.join("config", tenant)
        os.makedirs(config_dir)
        try:
            items = {"test": {"urls": ["https://example.com"]}}
            path = os.path.join(
                config_dir, "zia_url_categories.auto.tfvars.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write(render_tfvars(items))

            old = sys.stdout
            sys.stdout = io.StringIO()
            try:
                rc = main([tenant])
                out = sys.stdout.getvalue()
            finally:
                sys.stdout = old
        finally:
            shutil.rmtree(config_dir)

        self.assertEqual(rc, 1, "an error-bearing config must exit 1")
        self.assertIn("scheme", out)



class NonStringSetDuplicateTest(unittest.TestCase):
    def test_duplicate_ints_in_set_are_errors(self):
        # terraform dedupes sets regardless of member type — perma-drift
        from tools.lint import check_list_duplicates
        r = report()
        check_list_duplicates([3, 3], "rt", "w", r, is_set=True,
                              is_url_field=False)
        self.assertTrue(any("duplicate" in e for e in r.errors))

    def test_unhashable_members_are_skipped_gracefully(self):
        from tools.lint import check_list_duplicates
        r = report()
        check_list_duplicates([{"id": ["1"]}, {"id": ["1"]}], "rt", "w", r,
                              is_set=False, is_url_field=False)
        self.assertEqual(r.errors + r.warnings, [])



class EntityAllowanceTest(unittest.TestCase):
    def test_opted_out_resources_carry_entities_without_warning(self):
        from tools.lint import check_string
        r = report()
        check_string("old ----&gt; new", "zpa_app_connector_group", "w", r,
                     allow_entities=True)
        self.assertEqual(r.errors + r.warnings, [])



class ShadowingScaleTest(unittest.TestCase):
    def test_many_shadows_aggregate_to_one_warning_per_pair(self):
        # field-hit: a broad category legitimately containing parent
        # domains flooded thousands of per-pair lines; the rollup keeps
        # the signal (count + example) at one line per category pair
        items = {
            "broad": {"urls": ["dom%d.com" % i for i in range(50)]},
            "specific": {"urls": ["sub.dom%d.com" % i for i in range(50)]},
        }
        import os
        r = report()
        from tools.lint import check_category_shadowing
        os.environ["LINT_SHADOWING"] = "1"
        try:
            check_category_shadowing(items, "zia_url_categories", r)
        finally:
            del os.environ["LINT_SHADOWING"]
        self.assertEqual(len(r.warnings), 1)
        self.assertIn("50 entries", r.warnings[0])
        self.assertIn("broad", r.warnings[0])

    def test_verbose_env_restores_per_entry_lines(self):
        import os
        items = {
            "broad": {"urls": ["dom.com"]},
            "specific": {"urls": ["a.dom.com", "b.dom.com"]},
        }
        r = report()
        os.environ["LINT_SHADOWING_VERBOSE"] = "1"
        try:
            from tools.lint import check_category_shadowing
            check_category_shadowing(items, "zia_url_categories", r)
        finally:
            del os.environ["LINT_SHADOWING_VERBOSE"]
        self.assertEqual(len(r.warnings), 2)

    def test_indexed_scan_handles_tenant_scale_fast(self):
        # functional pin at realistic scale (the old pairwise scan took
        # minutes here; no timing assert — just completion + correctness)
        items = {"broad": {"urls": ["d%d.example.com" % i
                                    for i in range(5000)]}}
        items["spec"] = {"urls": ["s.d%d.example.com" % i
                                  for i in range(5000)]}
        r = report()
        from tools.lint import check_category_shadowing
        check_category_shadowing(items, "zia_url_categories", r)
        self.assertEqual(len(r.warnings), 1)  # the default summary pulse
        self.assertIn("5000 more-specific", r.warnings[0])



class AdvisoryModeTest(unittest.TestCase):
    """Fetched tenant data must REPORT errors without BLOCKING adoption
    (field-hit: the agent-side refresh ran lint against the tenant's own
    pre-existing whitespace/NBSP data and stopped the bootstrap)."""

    def _error_tenant(self, advisory):
        import json as _json
        import os
        import shutil
        from tools.lint import main
        from tools.transform import render_tfvars
        config_dir = os.path.join("config", "tmplintadv")
        shutil.rmtree(config_dir, ignore_errors=True)
        os.makedirs(config_dir)
        items = {"k": {"name": " padded ", "enabled": True}}
        with open(os.path.join(config_dir,
                               "zpa_segment_group.auto.tfvars.json"),
                  "w", encoding="utf-8") as f:
            f.write(render_tfvars(items))
        if advisory:
            os.environ["LINT_ADVISORY"] = "1"
        import io
        import sys
        old_out, sys.stdout = sys.stdout, io.StringIO()
        try:
            code = main(["tmplintadv"])
            out = sys.stdout.getvalue()
        finally:
            sys.stdout = old_out
            os.environ.pop("LINT_ADVISORY", None)
            shutil.rmtree(config_dir, ignore_errors=True)
        return code, out

    def test_strict_default_still_blocks(self):
        code, out = self._error_tenant(advisory=False)
        self.assertEqual(code, 1)
        self.assertIn("whitespace", out)

    def test_advisory_reports_everything_but_exits_0(self):
        code, out = self._error_tenant(advisory=True)
        self.assertEqual(code, 0)
        self.assertIn("whitespace", out)        # finding still printed
        self.assertIn("ADVISORY MODE", out)     # suppression is LOUD


if __name__ == "__main__":
    unittest.main()
