"""Tests for tools/cred_env.py — tenant-scoped credential remapping.

Two accepted spellings per standard var: tenant-first (ZS2_ZIA_USERNAME,
primary) and product-first (ZIA_ZS2_USERNAME, fallback). Tenant-first
wins on conflict, and the conflict is reported, never silent.
"""
import unittest

from tools.cred_env import candidate_names, resolve, shell_quote, tenant_prefix


class TenantPrefixTest(unittest.TestCase):
    def test_simple_label(self):
        self.assertEqual(tenant_prefix("zs2"), "ZS2_")

    def test_labels_sanitize_to_env_safe(self):
        self.assertEqual(tenant_prefix("gov-beta.1"), "GOV_BETA_1_")


class CandidateNamesTest(unittest.TestCase):
    def test_tenant_first_then_product_first(self):
        self.assertEqual(
            candidate_names("ZIA_USERNAME", "zs2"),
            ["ZS2_ZIA_USERNAME", "ZIA_ZS2_USERNAME"],
        )

    def test_multiword_rest_keeps_shape(self):
        self.assertEqual(
            candidate_names("ZSCALER_CLIENT_ID", "zs2"),
            ["ZS2_ZSCALER_CLIENT_ID", "ZSCALER_ZS2_CLIENT_ID"],
        )


class ResolveTest(unittest.TestCase):
    ENV = {
        "ZS2_ZSCALER_CLIENT_ID": "id-a",
        "ZS2_ZSCALER_CLIENT_SECRET": "sec-a",
        "ZS2_ZSCALER_CLOUD": "PRODUCTION",
        "ZS3_ZSCALER_CLIENT_ID": "id-b",
        "ZS2_NOT_A_REAL_VAR": "ignored",     # not on the allowlist
        "ZSCALER_CLIENT_ID": "unprefixed",   # no tenant -> not tenant-scoped
    }

    def test_picks_only_this_tenants_allowlisted_vars(self):
        pairs, conflicts = resolve(self.ENV, "zs2")
        self.assertEqual(
            dict(pairs),
            {
                "ZSCALER_CLIENT_ID": "id-a",
                "ZSCALER_CLIENT_SECRET": "sec-a",
                "ZSCALER_CLOUD": "PRODUCTION",
            },
        )
        self.assertEqual(conflicts, [])

    def test_other_tenant_resolves_independently(self):
        pairs, _ = resolve(self.ENV, "zs3")
        self.assertEqual(dict(pairs), {"ZSCALER_CLIENT_ID": "id-b"})

    def test_empty_environment_resolves_nothing(self):
        self.assertEqual(resolve({}, "zs2"), ([], []))

    def test_product_first_fallback_resolves(self):
        env = {"ZIA_ZS2_USERNAME": "admin@example.invalid"}
        pairs, conflicts = resolve(env, "zs2")
        self.assertEqual(dict(pairs), {"ZIA_USERNAME": "admin@example.invalid"})
        self.assertEqual(conflicts, [])

    def test_tenant_first_wins_and_conflict_reported(self):
        env = {
            "ZS2_ZIA_USERNAME": "tenant-first",
            "ZIA_ZS2_USERNAME": "product-first",
        }
        pairs, conflicts = resolve(env, "zs2")
        self.assertEqual(dict(pairs), {"ZIA_USERNAME": "tenant-first"})
        self.assertEqual(conflicts, ["ZIA_USERNAME"])

    def test_both_spellings_same_value_is_not_a_conflict(self):
        env = {
            "ZS2_ZIA_USERNAME": "same",
            "ZIA_ZS2_USERNAME": "same",
        }
        pairs, conflicts = resolve(env, "zs2")
        self.assertEqual(dict(pairs), {"ZIA_USERNAME": "same"})
        self.assertEqual(conflicts, [])

    def test_product_first_never_matches_unscoped_standard_var(self):
        # ZIA_USERNAME with no tenant token anywhere must NOT resolve —
        # only the two tenant-scoped spellings count.
        pairs, _ = resolve({"ZIA_USERNAME": "plain"}, "zs2")
        self.assertEqual(pairs, [])


class ShellQuoteTest(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(shell_quote("abc"), "'abc'")

    def test_embedded_single_quote(self):
        self.assertEqual(shell_quote("a'b"), "'a'\"'\"'b'")



class EmptyValueTest(unittest.TestCase):
    def test_set_but_empty_var_is_not_resolved(self):
        # resolving '' would push the failure to the fetch layer, whose
        # "missing required env var" message misleads when the var IS set
        from tools.cred_env import resolve
        pairs, conflicts = resolve({"ZS2_ZIA_PASSWORD": ""}, "zs2")
        self.assertEqual(pairs, [])
        self.assertEqual(conflicts, [])


if __name__ == "__main__":
    unittest.main()
