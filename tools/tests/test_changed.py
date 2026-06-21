"""Tests for tools/changed.py — diff-to-plan-target derivation.

The mapping is the safety property of the scoped pipeline: a changed
config file must select exactly its own (tenant, type) pair, module
changes must fan out only to tenants that actually carry config, and
shared-machinery changes must select everything — over-planning is
safe (plans are read-only), under-planning is not.
"""
import io
import sys
import unittest

from tools import changed
from tools.changed import pairs_from_paths

PAIRS = {
    ("zs2", "zia_rule_labels"),
    ("zs2", "zia_url_categories"),
    ("zs3", "zia_rule_labels"),
}


class PairsFromPathsTest(unittest.TestCase):
    def test_config_change_selects_its_pair(self):
        out = pairs_from_paths(
            ["config/zs2/zia_rule_labels.auto.tfvars.json"], PAIRS
        )
        self.assertEqual(out, {("zs2", "zia_rule_labels")})

    def test_lookup_output_change_selects_its_pair(self):
        out = pairs_from_paths(
            ["lookups/zs2/zia_url_categories.lookup.json"], PAIRS
        )
        self.assertEqual(out, {("zs2", "zia_url_categories")})

    def test_legacy_lookup_change_selects_its_pair(self):
        out = pairs_from_paths(
            ["config/zs2/zia_url_categories.lookup.json"], PAIRS
        )
        self.assertEqual(out, {("zs2", "zia_url_categories")})

    def test_imports_change_selects_its_pair(self):
        out = pairs_from_paths(
            ["imports/zs2/zia_url_categories_imports.tf"], PAIRS
        )
        self.assertEqual(out, {("zs2", "zia_url_categories")})

    def test_env_root_change_selects_its_pair(self):
        out = pairs_from_paths(
            ["envs/zs3/zia_rule_labels/main.tf"], PAIRS
        )
        self.assertEqual(out, {("zs3", "zia_rule_labels")})

    def test_moves_only_change_selects_its_pair(self):
        # A _moves.tf-only diff (a rename expressed as moved blocks, with no
        # config or imports change) must still plan its pair — otherwise the
        # move is never applied and state drifts from the address it names.
        out = pairs_from_paths(
            ["imports/zs2/zia_url_categories_moves.tf"], PAIRS
        )
        self.assertEqual(out, {("zs2", "zia_url_categories")})

    def test_module_change_fans_out_to_configured_tenants_only(self):
        # zia_rule_labels has config on zs2 AND zs3; both pairs selected.
        out = pairs_from_paths(["modules/zia_rule_labels/main.tf"], PAIRS)
        self.assertEqual(
            out, {("zs2", "zia_rule_labels"), ("zs3", "zia_rule_labels")}
        )

    def test_module_without_config_selects_nothing(self):
        out = pairs_from_paths(["modules/zpa_segment_group/main.tf"], PAIRS)
        self.assertEqual(out, set())

    def test_shared_machinery_selects_everything(self):
        for path in ("tools/transform.py", "schemas/provider/zia.json", "Makefile"):
            self.assertEqual(pairs_from_paths([path], PAIRS), PAIRS, path)

    def test_docs_select_nothing(self):
        out = pairs_from_paths(["README.md", "RUNBOOK.md", "docs/notes.md"], PAIRS)
        self.assertEqual(out, set())

    def test_unknown_pair_never_emitted(self):
        # Config bounds every expansion: a path naming a pair with no
        # committed config (typo, rename, deleted tenant) selects nothing.
        out = pairs_from_paths(
            [
                "config/nope/zia_rule_labels.auto.tfvars.json",
                "config/zs2/zia_nonexistent.auto.tfvars.json",
                "envs/zs2/.backend",
            ],
            PAIRS,
        )
        self.assertEqual(out, set())

    def test_mixed_change_unions(self):
        out = pairs_from_paths(
            [
                "config/zs2/zia_url_categories.auto.tfvars.json",
                "imports/zs3/zia_rule_labels_imports.tf",
                "README.md",
            ],
            PAIRS,
        )
        self.assertEqual(
            out, {("zs2", "zia_url_categories"), ("zs3", "zia_rule_labels")}
        )


class DerivedExpansionTest(unittest.TestCase):
    """A change to a SOURCE type also plans its derived dependents. The
    reorder mutates rule order on apply and its config no longer travels with
    the rules (order is deprecated off the access rule), so a rule change can
    move ordering without touching the reorder config — plan it anyway, so the
    mutation is in a plan the reviewer sees. Uses the real registry, where
    zpa_policy_access_rule_reorder derives from zpa_policy_access_rule."""

    PAIRS = {
        ("demo", "zpa_policy_access_rule"),
        ("demo", "zpa_policy_access_rule_reorder"),
        ("demo", "zia_rule_labels"),
    }

    def test_source_config_change_also_plans_reorder(self):
        out = pairs_from_paths(
            ["config/demo/zpa_policy_access_rule.auto.tfvars.json"], self.PAIRS
        )
        self.assertEqual(out, {("demo", "zpa_policy_access_rule"),
                               ("demo", "zpa_policy_access_rule_reorder")})

    def test_source_module_change_also_plans_reorder(self):
        out = pairs_from_paths(
            ["modules/zpa_policy_access_rule/main.tf"], self.PAIRS
        )
        self.assertEqual(out, {("demo", "zpa_policy_access_rule"),
                               ("demo", "zpa_policy_access_rule_reorder")})

    def test_reorder_change_does_not_pull_in_source(self):
        # Expansion is one-directional: a reorder-only change plans only the
        # reorder (the rules' content is unaffected by reordering them).
        out = pairs_from_paths(
            ["config/demo/zpa_policy_access_rule_reorder.auto.tfvars.json"],
            self.PAIRS,
        )
        self.assertEqual(out, {("demo", "zpa_policy_access_rule_reorder")})

    def test_derived_skipped_when_not_plannable(self):
        # A tenant that carries the source but has no reorder root/config:
        # the derived pair isn't in `plannable`, so it is not invented.
        out = pairs_from_paths(
            ["config/demo/zpa_policy_access_rule.auto.tfvars.json"],
            {("demo", "zpa_policy_access_rule")},
        )
        self.assertEqual(out, {("demo", "zpa_policy_access_rule")})

    def test_non_source_change_is_unaffected(self):
        out = pairs_from_paths(
            ["config/demo/zia_rule_labels.auto.tfvars.json"], self.PAIRS
        )
        self.assertEqual(out, {("demo", "zia_rule_labels")})


class DiscoverConfigPairsTest(unittest.TestCase):
    def test_discovers_committed_demo_pairs(self):
        # The committed demo tenant must be discovered (registry-bounded).
        # Deliberately TENANT-TOLERANT: in private deployment repos real
        # tenants are committed alongside demo, and this suite runs there
        # too — never assert demo is the only tenant (field-broke once).
        from tools.changed import discover_config_pairs
        from tools.registry import generated_types

        pairs = discover_config_pairs()
        self.assertIn(("demo", "zia_rule_labels"), pairs)
        types = set(generated_types())
        for _tenant, rt in pairs:
            self.assertIn(rt, types, "non-registry type discovered: %r" % rt)


class DiscoverEnvRootPairsTest(unittest.TestCase):
    def test_discovers_committed_demo_env_roots(self):
        # Mirrors DiscoverConfigPairsTest: the committed demo env roots must
        # be discovered, registry-bounded. Same TENANT-TOLERANCE rule — real
        # deployment repos carry extra tenants and run this suite too.
        from tools.changed import discover_env_root_pairs
        from tools.registry import generated_types

        pairs = discover_env_root_pairs()
        self.assertIn(("demo", "zia_rule_labels"), pairs)
        types = set(generated_types())
        for _tenant, rt in pairs:
            self.assertIn(rt, types, "non-registry type discovered: %r" % rt)


class MainDeletionPlannableTest(unittest.TestCase):
    def test_deleted_config_still_plans_via_env_root(self):
        # Codex gap: a commit that DELETES a config file leaves the env root
        # in place; the diff still names the (now-absent) config path. The
        # pair is gone from discover_config_pairs() but present in
        # discover_env_root_pairs(), and main() unions the two — so the pair
        # is still selected and the destroy is surfaced, not silently dropped.
        orig_paths = changed.changed_paths
        orig_cfg = changed.discover_config_pairs
        orig_env = changed.discover_env_root_pairs
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
        changed.changed_paths = lambda base_ref: [
            "config/demo/zia_rule_labels.auto.tfvars.json"
        ]
        changed.discover_config_pairs = lambda: set()  # config deleted
        changed.discover_env_root_pairs = lambda: {("demo", "zia_rule_labels")}
        try:
            rc = changed.main(["HEAD~1"])
            out = sys.stdout.getvalue()
        finally:
            changed.changed_paths = orig_paths
            changed.discover_config_pairs = orig_cfg
            changed.discover_env_root_pairs = orig_env
            sys.stdout, sys.stderr = old_out, old_err
        self.assertEqual(rc, 0)
        self.assertEqual(out, "demo zia_rule_labels\n")


class MainTenantFilterTest(unittest.TestCase):
    """--tenant scopes output to one tenant — a per-tenant delivery pipeline
    must not emit a foreign tenant a cross-tenant merge happened to touch."""

    def _run(self, paths, argv):
        orig_paths = changed.changed_paths
        orig_cfg = changed.discover_config_pairs
        orig_env = changed.discover_env_root_pairs
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
        changed.changed_paths = lambda base_ref: paths
        changed.discover_config_pairs = lambda: {
            ("zs2", "zia_rule_labels"),
            ("zs3", "zia_rule_labels"),
        }
        changed.discover_env_root_pairs = lambda: set()
        try:
            rc = changed.main(argv)
            return rc, sys.stdout.getvalue(), sys.stderr.getvalue()
        finally:
            changed.changed_paths = orig_paths
            changed.discover_config_pairs = orig_cfg
            changed.discover_env_root_pairs = orig_env
            sys.stdout, sys.stderr = old_out, old_err

    def test_tenant_filter_keeps_only_named_tenant(self):
        # A module change fans out to BOTH tenants; --tenant zs2 keeps one.
        rc, out, _ = self._run(
            ["modules/zia_rule_labels/main.tf"], ["HEAD~1", "--tenant", "zs2"]
        )
        self.assertEqual(rc, 0)
        self.assertEqual(out, "zs2 zia_rule_labels\n")

    def test_unfiltered_emits_both_tenants(self):
        rc, out, _ = self._run(
            ["modules/zia_rule_labels/main.tf"], ["HEAD~1"]
        )
        self.assertEqual(rc, 0)
        self.assertEqual(out, "zs2 zia_rule_labels\nzs3 zia_rule_labels\n")

    def test_tenant_with_no_changes_is_empty_success(self):
        # zs3's config didn't change; scoping to it yields nothing, exit 0.
        rc, out, err = self._run(
            ["config/zs2/zia_rule_labels.auto.tfvars.json"],
            ["HEAD~1", "--tenant", "zs3"],
        )
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")
        self.assertIn("for tenant zs3", err)

    def test_missing_base_ref_is_usage_error(self):
        rc, _, err = self._run([], ["--tenant", "zs2"])
        self.assertEqual(rc, 2)
        self.assertIn("usage", err)

    def test_extra_positional_is_usage_error(self):
        rc, _, err = self._run([], ["HEAD~1", "HEAD~2"])
        self.assertEqual(rc, 2)
        self.assertIn("usage", err)


class MainEmptyDiffTest(unittest.TestCase):
    def test_docs_only_diff_exits_0(self):
        # Contract: an empty plan-target set (docs-only diff) is success,
        # not failure — a PR pipeline must treat exit 0 / no output as
        # "nothing plannable", not as an error.
        orig = changed.changed_paths
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
        changed.changed_paths = lambda base_ref: ["README.md", "docs/x.md"]
        try:
            rc = changed.main(["HEAD~1"])
            out = sys.stdout.getvalue()
        finally:
            changed.changed_paths = orig
            sys.stdout, sys.stderr = old_out, old_err
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
