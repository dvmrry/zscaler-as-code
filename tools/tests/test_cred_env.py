"""Tests for tools/cred_env.py — tenant-scoped credential remapping.

Two accepted spellings per standard var: tenant-first (ZS2_ZIA_USERNAME,
primary) and product-first (ZIA_ZS2_USERNAME, fallback). Tenant-first
wins on conflict, and the conflict is reported, never silent.

Beyond resolution, cred_env scopes export to ONE auth mode (legacy vs
OneAPI — never mixed), preflights completeness per product (a half-set
product is a loud error, an absent product is fine), normalizes the mode
flag, and prints a secret-safe debug summary. ZCC has no legacy path
(OneAPI-only), so no ZCC-specific vars exist anymore.
"""
import io
import sys
import unittest

from tools.cred_env import (
    candidate_names, resolve, scoped_export, select_mode, shell_quote,
    tenant_prefix, main,
)


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


# --- Auth-mode resolution (legacy vs OneAPI; never mixed) ----------------

ONEAPI_FULL = {
    "ZSCALER_CLIENT_ID": "cid", "ZSCALER_CLIENT_SECRET": "sec",
    "ZSCALER_VANITY_DOMAIN": "acme",
}
LEGACY_ZIA_FULL = {
    "ZIA_USERNAME": "u", "ZIA_PASSWORD": "p", "ZIA_API_KEY": "k",
    "ZIA_CLOUD": "zscalertwo",
}
LEGACY_ZPA_FULL = {
    "ZPA_CLIENT_ID": "zc", "ZPA_CLIENT_SECRET": "zs", "ZPA_CUSTOMER_ID": "C9",
    "ZPA_CLOUD": "ZPATWO",
}


class SelectModeTest(unittest.TestCase):
    def test_explicit_legacy_flag(self):
        mode, warn = select_mode({"ZSCALER_USE_LEGACY_CLIENT": "true"})
        self.assertEqual(mode, "legacy")
        self.assertFalse(warn)

    def test_explicit_falsey_flag_is_oneapi(self):
        mode, warn = select_mode({"ZSCALER_USE_LEGACY_CLIENT": "false"})
        self.assertEqual(mode, "oneapi")
        self.assertFalse(warn)

    def test_unset_defaults_to_oneapi(self):
        mode, warn = select_mode(dict(ONEAPI_FULL))
        self.assertEqual(mode, "oneapi")
        self.assertFalse(warn)

    def test_unset_but_legacy_creds_present_warns(self):
        # a legacy cred with no mode flag is a likely misconfiguration:
        # default OneAPI but flag it loudly rather than silently.
        mode, warn = select_mode(dict(LEGACY_ZIA_FULL))
        self.assertEqual(mode, "oneapi")
        self.assertTrue(warn)


class ScopedExportOneApiTest(unittest.TestCase):
    def test_complete_oneapi_exports_only_oneapi_vars(self):
        resolved = dict(ONEAPI_FULL)
        resolved.update(LEGACY_ZIA_FULL)  # stray legacy vars must NOT leak
        out, missing, has = scoped_export(resolved, "oneapi")
        names = dict(out)
        self.assertEqual(missing, [])
        self.assertTrue(has)
        self.assertEqual(names["ZSCALER_CLIENT_ID"], "cid")
        self.assertNotIn("ZIA_USERNAME", names)  # legacy never mixed in
        self.assertNotIn("ZIA_CLOUD", names)
        # mode flag is normalized and always emitted
        self.assertEqual(names["ZSCALER_USE_LEGACY_CLIENT"], "false")

    def test_partial_oneapi_is_a_loud_gap(self):
        out, missing, has = scoped_export(
            {"ZSCALER_CLIENT_ID": "cid"}, "oneapi")
        self.assertFalse(has)
        self.assertEqual(len(missing), 1)
        group, gaps = missing[0]
        self.assertIn("ZSCALER_CLIENT_SECRET", gaps)
        self.assertIn("ZSCALER_VANITY_DOMAIN", gaps)

    def test_optional_oneapi_vars_pass_through_when_present(self):
        resolved = dict(ONEAPI_FULL)
        resolved["ZPA_CUSTOMER_ID"] = "C9"
        resolved["ZSCALER_CLOUD"] = "beta"
        out, missing, has = scoped_export(resolved, "oneapi")
        names = dict(out)
        self.assertEqual(names["ZPA_CUSTOMER_ID"], "C9")
        self.assertEqual(names["ZSCALER_CLOUD"], "beta")

class ScopedExportLegacyTest(unittest.TestCase):
    def test_both_products_complete(self):
        resolved = dict(LEGACY_ZIA_FULL)
        resolved.update(LEGACY_ZPA_FULL)
        resolved.update(ONEAPI_FULL)  # stray OneAPI vars must NOT leak
        out, missing, has = scoped_export(resolved, "legacy")
        names = dict(out)
        self.assertEqual(missing, [])
        self.assertTrue(has)
        self.assertEqual(names["ZIA_API_KEY"], "k")
        self.assertEqual(names["ZPA_CLOUD"], "ZPATWO")
        self.assertNotIn("ZSCALER_CLIENT_ID", names)  # OneAPI never mixed in
        self.assertEqual(names["ZSCALER_USE_LEGACY_CLIENT"], "true")

    def test_absent_product_is_fine(self):
        # ZIA-only legacy tenant: ZPA simply absent, not an error.
        out, missing, has = scoped_export(dict(LEGACY_ZIA_FULL), "legacy")
        names = dict(out)
        self.assertEqual(missing, [])
        self.assertTrue(has)
        self.assertIn("ZIA_USERNAME", names)
        self.assertNotIn("ZPA_CLIENT_ID", names)

    def test_partial_product_is_a_loud_gap(self):
        # ZPA half-configured (cloud + id but no secret/customer) -> error,
        # naming exactly what is missing, even though ZIA is complete.
        resolved = dict(LEGACY_ZIA_FULL)
        resolved.update({"ZPA_CLIENT_ID": "zc", "ZPA_CLOUD": "ZPATWO"})
        out, missing, has = scoped_export(resolved, "legacy")
        self.assertEqual(len(missing), 1)
        group, gaps = missing[0]
        self.assertEqual(group, "ZPA")
        self.assertIn("ZPA_CLIENT_SECRET", gaps)
        self.assertIn("ZPA_CUSTOMER_ID", gaps)

    def test_no_products_at_all_has_no_creds(self):
        out, missing, has = scoped_export({}, "legacy")
        self.assertEqual(missing, [])
        self.assertFalse(has)

    def test_legacy_base_override_passes_through(self):
        resolved = dict(LEGACY_ZPA_FULL)
        resolved["ZPA_LEGACY_BASE_URL"] = "https://config.zpatwo.net"
        out, _, has = scoped_export(resolved, "legacy")
        self.assertEqual(dict(out)["ZPA_LEGACY_BASE_URL"],
                         "https://config.zpatwo.net")


class MainEndToEndTest(unittest.TestCase):
    """main() emits export lines to stdout, debug to stderr, and fails
    loud (exit 1) on incomplete/absent creds without emitting exports."""

    def _run(self, tenant, environ):
        out, err = io.StringIO(), io.StringIO()
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            rc = main([tenant], environ=environ)
        finally:
            sys.stdout, sys.stderr = old_out, old_err
        return rc, out.getvalue(), err.getvalue()

    def test_oneapi_emits_exports_and_safe_debug(self):
        env = {
            "ZS2_ZSCALER_CLIENT_ID": "cid", "ZS2_ZSCALER_CLIENT_SECRET": "sec",
            "ZS2_ZSCALER_VANITY_DOMAIN": "acme", "ZS2_ZSCALER_CLOUD": "beta",
        }
        rc, out, err = self._run("zs2", env)
        self.assertEqual(rc, 0)
        self.assertIn("export ZSCALER_CLIENT_ID='cid'", out)
        self.assertIn("export ZSCALER_USE_LEGACY_CLIENT='false'", out)
        # debug shows safe values but never the secret value
        self.assertIn("beta", err)             # cloud is safe to show
        self.assertNotIn("sec", err)           # client secret value redacted
        self.assertIn("oneapi", err.lower())

    def test_legacy_emits_and_normalizes_flag(self):
        env = dict(LEGACY_ZIA_FULL)
        env.update(LEGACY_ZPA_FULL)
        env = {"ZS2_" + k: v for k, v in env.items()}
        env["ZS2_ZSCALER_USE_LEGACY_CLIENT"] = "1"  # any truthy spelling
        rc, out, err = self._run("zs2", env)
        self.assertEqual(rc, 0)
        self.assertIn("export ZSCALER_USE_LEGACY_CLIENT='true'", out)
        self.assertIn("export ZIA_CLOUD='zscalertwo'", out)
        self.assertNotIn("export ZSCALER_CLIENT_ID", out)  # mode-scoped

    def test_incomplete_oneapi_fails_loud_without_exports(self):
        env = {"ZS2_ZSCALER_CLIENT_ID": "cid"}  # secret + vanity missing
        rc, out, err = self._run("zs2", env)
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")              # nothing exported on failure
        self.assertIn("ZSCALER_CLIENT_SECRET", err)
        self.assertIn("ZSCALER_VANITY_DOMAIN", err)

    def test_partial_legacy_product_fails_loud(self):
        env = {"ZS2_" + k: v for k, v in LEGACY_ZIA_FULL.items()}
        env["ZS2_ZSCALER_USE_LEGACY_CLIENT"] = "true"
        env["ZS2_ZPA_CLIENT_ID"] = "zc"        # ZPA half-set -> gap
        rc, out, err = self._run("zs2", env)
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertIn("ZPA_CLIENT_SECRET", err)

    def test_no_creds_at_all_fails_loud(self):
        rc, out, err = self._run("zs2", {})
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")

    def test_identifying_vars_masked_in_debug_by_default(self):
        # vanity domain + customer id are non-secret but tenant-identifying:
        # hidden in the debug stream unless FETCH_DEBUG is set. (They still
        # appear in the eval'd stdout — that is the whole point of it.)
        env = {
            "ZS2_ZSCALER_CLIENT_ID": "cid", "ZS2_ZSCALER_CLIENT_SECRET": "sec",
            "ZS2_ZSCALER_VANITY_DOMAIN": "acmecorp",
            "ZS2_ZPA_CUSTOMER_ID": "C12345",
        }
        rc, out, err = self._run("zs2", env)
        self.assertEqual(rc, 0)
        self.assertIn("acmecorp", out)               # exported for the providers
        self.assertNotIn("acmecorp", err)            # but hidden in debug
        self.assertNotIn("C12345", err)
        self.assertIn("FETCH_DEBUG", err)            # reveal hint

    def test_identifying_vars_revealed_with_fetch_debug(self):
        env = {
            "ZS2_ZSCALER_CLIENT_ID": "cid",
            "ZS2_ZSCALER_CLIENT_SECRET": "topsecret",
            "ZS2_ZSCALER_VANITY_DOMAIN": "acmecorp",
            "ZS2_ZPA_CUSTOMER_ID": "C12345",
            "FETCH_DEBUG": "1",
        }
        rc, out, err = self._run("zs2", env)
        self.assertIn("acmecorp", err)               # revealed
        self.assertIn("C12345", err)
        self.assertNotIn("topsecret", err)           # secret still never in debug

    def test_secret_values_never_reach_stdout_or_debug(self):
        env = {"ZS2_" + k: v for k, v in dict(LEGACY_ZIA_FULL).items()}
        env["ZS2_ZSCALER_USE_LEGACY_CLIENT"] = "true"
        rc, out, err = self._run("zs2", env)
        # the obfuscation key value appears in the eval'd stdout (by design)
        # but NEVER in the human-facing debug stream
        self.assertIn("export ZIA_API_KEY='k'", out)
        self.assertNotIn("api_key='k'", err.lower())
        self.assertNotIn(": k", err)


if __name__ == "__main__":
    unittest.main()
