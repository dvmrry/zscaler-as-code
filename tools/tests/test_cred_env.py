"""Tests for tools/cred_env.py — tenant-prefixed credential remapping."""
import unittest

from tools.cred_env import resolve, shell_quote, tenant_prefix


class TenantPrefixTest(unittest.TestCase):
    def test_simple_label(self):
        self.assertEqual(tenant_prefix("zs2"), "ZS2_")

    def test_labels_sanitize_to_env_safe(self):
        self.assertEqual(tenant_prefix("gov-beta.1"), "GOV_BETA_1_")


class ResolveTest(unittest.TestCase):
    ENV = {
        "ZS2_ZSCALER_CLIENT_ID": "id-a",
        "ZS2_ZSCALER_CLIENT_SECRET": "sec-a",
        "ZS2_ZSCALER_CLOUD": "PRODUCTION",
        "ZS3_ZSCALER_CLIENT_ID": "id-b",
        "ZS2_NOT_A_REAL_VAR": "ignored",     # not on the allowlist
        "ZSCALER_CLIENT_ID": "unprefixed",   # no prefix -> not tenant-scoped
    }

    def test_picks_only_this_tenants_allowlisted_vars(self):
        out = dict(resolve(self.ENV, "zs2"))
        self.assertEqual(
            out,
            {
                "ZSCALER_CLIENT_ID": "id-a",
                "ZSCALER_CLIENT_SECRET": "sec-a",
                "ZSCALER_CLOUD": "PRODUCTION",
            },
        )

    def test_other_tenant_resolves_independently(self):
        out = dict(resolve(self.ENV, "zs3"))
        self.assertEqual(out, {"ZSCALER_CLIENT_ID": "id-b"})

    def test_empty_environment_resolves_nothing(self):
        self.assertEqual(resolve({}, "zs2"), [])


class ShellQuoteTest(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(shell_quote("abc"), "'abc'")

    def test_embedded_single_quote(self):
        self.assertEqual(shell_quote("a'b"), "'a'\"'\"'b'")


if __name__ == "__main__":
    unittest.main()
