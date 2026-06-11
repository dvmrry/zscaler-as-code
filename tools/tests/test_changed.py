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


class DiscoverConfigPairsTest(unittest.TestCase):
    def test_discovers_committed_demo_pairs(self):
        # The committed demo tenant must be discovered (registry-bounded).
        from tools.changed import discover_config_pairs

        pairs = discover_config_pairs()
        self.assertIn(("demo", "zia_rule_labels"), pairs)
        self.assertTrue(
            all(t == "demo" for t, _ in pairs),
            "unexpected tenant in config_pairs",
        )


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
