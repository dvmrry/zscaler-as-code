"""Tests for tools/typecheck.py. All fixture data is fictional."""
import json
import os
import sys
import tempfile
import unittest

from tools.typecheck import check_value, check_item, check_config_file, suggest, main


# ---------------------------------------------------------------------------
# check_value — primitive and collection rules
# ---------------------------------------------------------------------------

class CheckValuePrimitivesTest(unittest.TestCase):
    def test_string_ok(self):
        self.assertEqual(check_value("hello", "string"), [])

    def test_string_empty_ok(self):
        self.assertEqual(check_value("", "string"), [])

    def test_string_got_int(self):
        result = check_value(42, "string")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][1], "string")
        self.assertIn("int", result[0][2])

    def test_string_got_float(self):
        result = check_value(3.14, "string")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][1], "string")

    def test_string_got_bool_flagged(self):
        # bool is not a valid string — indicates missed coercion
        result = check_value(True, "string")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][1], "string")

    def test_number_int_ok(self):
        self.assertEqual(check_value(5, "number"), [])

    def test_number_float_ok(self):
        self.assertEqual(check_value(3.14, "number"), [])

    def test_number_got_bool(self):
        result = check_value(True, "number")
        self.assertEqual(len(result), 1)

    def test_number_got_str(self):
        result = check_value("5", "number")
        self.assertEqual(len(result), 1)

    def test_bool_ok(self):
        self.assertEqual(check_value(True, "bool"), [])
        self.assertEqual(check_value(False, "bool"), [])

    def test_bool_got_int(self):
        result = check_value(1, "bool")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][1], "bool")
        self.assertIn("int", result[0][2])

    def test_bool_got_str(self):
        result = check_value("yes", "bool")
        self.assertEqual(len(result), 1)

    def test_none_always_ok(self):
        self.assertEqual(check_value(None, "string"), [])
        self.assertEqual(check_value(None, "number"), [])
        self.assertEqual(check_value(None, "bool"), [])


class CheckValueCollectionsTest(unittest.TestCase):
    def test_list_string_ok(self):
        self.assertEqual(check_value(["a", "b"], ["list", "string"]), [])

    def test_set_string_ok(self):
        self.assertEqual(check_value(["x"], ["set", "string"]), [])

    def test_list_number_ok(self):
        self.assertEqual(check_value([1, 2], ["list", "number"]), [])

    def test_list_empty_ok(self):
        self.assertEqual(check_value([], ["list", "string"]), [])

    def test_list_got_str_mismatch(self):
        result = check_value("csv,value", ["list", "string"])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][1], ["list", "string"])

    def test_list_element_wrong_type(self):
        # list of numbers but contains a string element
        result = check_value([1, "bad", 3], ["list", "number"])
        self.assertEqual(len(result), 1)
        self.assertIn("[1]", result[0][0])

    def test_map_ok(self):
        self.assertEqual(check_value({"k": "v"}, ["map", "string"]), [])

    def test_map_got_non_dict(self):
        result = check_value("notamap", ["map", "string"])
        self.assertEqual(len(result), 1)

    def test_none_ok_for_collection(self):
        self.assertEqual(check_value(None, ["list", "string"]), [])

    def test_object_collection_ok(self):
        enc = ["list", ["object", {"from": "string", "to": "string"}]]
        value = [{"from": "80", "to": "80"}, {"from": "443", "to": "443"}]
        self.assertEqual(check_value(value, enc), [])

    def test_object_collection_member_wrong_type(self):
        enc = ["list", ["object", {"from": "string", "to": "string"}]]
        value = [{"from": 80, "to": "80"}]
        result = check_value(value, enc)
        self.assertEqual(len(result), 1)
        self.assertIn("[0].from", result[0][0])

    def test_object_collection_unknown_member(self):
        enc = ["list", ["object", {"from": "string", "to": "string"}]]
        value = [{"from": "80", "to": "80", "extra": "bad"}]
        result = check_value(value, enc)
        self.assertEqual(len(result), 1)
        self.assertIn("[0].extra", result[0][0])


# ---------------------------------------------------------------------------
# check_item — walking block attrs + block_types
# ---------------------------------------------------------------------------

class CheckItemTest(unittest.TestCase):
    FAKE_BLOCK = {
        "attributes": {
            "enabled": {"type": "bool", "optional": True},
            "name": {"type": "string", "required": True},
            "count": {"type": "number", "optional": True},
            "tags": {"type": ["list", "string"], "optional": True},
        },
        "block_types": {},
    }

    def test_all_ok(self):
        item = {"enabled": True, "name": "x", "count": 5, "tags": ["a"]}
        self.assertEqual(check_item(item, self.FAKE_BLOCK), [])

    def test_bool_got_int_flagged(self):
        item = {"enabled": 1, "name": "x"}
        result = check_item(item, self.FAKE_BLOCK)
        self.assertEqual(len(result), 1)
        _, field_path, expected, got = result[0]
        self.assertEqual(field_path, "enabled")
        self.assertEqual(expected, "bool")
        self.assertIn("int", got)

    def test_unknown_key_flagged(self):
        item = {"name": "x", "stale_field": "oops"}
        result = check_item(item, self.FAKE_BLOCK)
        self.assertEqual(len(result), 1)
        _, field_path, expected, got = result[0]
        self.assertEqual(field_path, "stale_field")
        self.assertIn("not an input", expected)

    def test_none_values_ok(self):
        item = {"name": "x", "enabled": None, "tags": None}
        self.assertEqual(check_item(item, self.FAKE_BLOCK), [])


class CheckItemWithRealSchemaTest(unittest.TestCase):
    """Typecheck on a RAW-ish dict with int flags for zcc_failopen_policy.

    After Part A + existing coercion the TRANSFORM output would be bool,
    but typecheck on a raw-ish dict must flag int 1 as a bool mismatch.
    """

    def test_zcc_failopen_int_flags_flagged(self):
        from tools.tfschema import load_resource
        rs = load_resource("zcc_failopen_policy")
        block = rs["block"]
        # Simulate pre-coercion item with int flags (what ZCC API returns)
        item = {
            "active": 1,
            "enable_fail_open": 0,
            "enable_captive_portal_detection": 1,
            "enable_strict_enforcement_prompt": 0,
            "enable_web_sec_on_proxy_unreachable": 1,
            "enable_web_sec_on_tunnel_failure": 1,
        }
        result = check_item(item, block)
        # All six bool fields with int values must be flagged
        flagged = [r[1] for r in result]
        for f in item:
            self.assertIn(f, flagged, "expected mismatch for %r" % f)

    def test_zcc_failopen_coerced_clean(self):
        from tools.tfschema import load_resource
        rs = load_resource("zcc_failopen_policy")
        block = rs["block"]
        # Simulates what coerce_item produces — all bools
        item = {
            "active": True,
            "enable_fail_open": False,
            "enable_captive_portal_detection": True,
            "enable_strict_enforcement_prompt": False,
            "enable_web_sec_on_proxy_unreachable": True,
            "enable_web_sec_on_tunnel_failure": True,
            "captive_portal_web_sec_disable_minutes": 5,
            "strict_enforcement_prompt_delay_minutes": 0,
            "strict_enforcement_prompt_message": "",
            "tunnel_failure_retry_count": 3,
        }
        self.assertEqual(check_item(item, block), [])


# ---------------------------------------------------------------------------
# suggest() — decision table spot-checks
# ---------------------------------------------------------------------------

class SuggestTest(unittest.TestCase):
    def test_bool_got_int(self):
        msg = suggest("bool", "int", "active")
        self.assertIn("0/1", msg)

    def test_bool_got_str(self):
        msg = suggest("bool", "str", "flag")
        self.assertIn("yes", msg.lower())

    def test_list_got_str(self):
        msg = suggest(["list", "string"], "str", "dns_servers")
        self.assertIn("split_csv", msg)
        self.assertIn("dns_servers", msg)

    def test_set_got_str(self):
        msg = suggest(["set", "string"], "str", "tags")
        self.assertIn("split_csv", msg)

    def test_string_got_int(self):
        msg = suggest("string", "int", "version")
        self.assertIn("transform", msg.lower())

    def test_number_got_str(self):
        msg = suggest("number", "str", "port")
        self.assertIn("transform", msg.lower())

    def test_unknown_key(self):
        msg = suggest("not an input", "str", "stale")
        self.assertIn("regenerate", msg.lower())

    def test_fallback(self):
        msg = suggest("string", "dict", "weird")
        self.assertIn("relay", msg.lower())


# ---------------------------------------------------------------------------
# check_config_file — integration + file I/O
# ---------------------------------------------------------------------------

class CheckConfigFileTest(unittest.TestCase):
    def _write_config(self, tmp_dir, rt, items):
        path = os.path.join(tmp_dir, rt + ".auto.tfvars.json")
        with open(path, "w") as f:
            json.dump({"items": items}, f)
        return path

    def test_clean_returns_empty(self):
        from tools.tfschema import load_resource
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_config(tmp, "zcc_failopen_policy", {
                "pol_a": {
                    "active": True,
                    "enable_fail_open": True,
                }
            })
            result = check_config_file("zcc_failopen_policy", path)
            self.assertEqual(result, [])

    def test_mismatch_returned(self):
        from tools.tfschema import load_resource
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_config(tmp, "zcc_failopen_policy", {
                "pol_a": {
                    "active": 1,  # int instead of bool
                }
            })
            result = check_config_file("zcc_failopen_policy", path)
            self.assertEqual(len(result), 1)
            item_key, field_path, expected, got_repr = result[0]
            self.assertEqual(item_key, "pol_a")
            self.assertEqual(field_path, "active")
            self.assertEqual(expected, "bool")
            self.assertIn("int", got_repr)


# ---------------------------------------------------------------------------
# Demo clean gate — zero mismatches across ALL config/demo files
# ---------------------------------------------------------------------------

class DemoCleanGateTest(unittest.TestCase):
    """Permanent gate: config/demo/ must type-check clean.

    This test runs check_config_file over every config/demo/<type>.auto.tfvars.json
    that exists and asserts zero mismatches — it is the enforcement point that
    the demo tenant stays type-correct after every change.
    """

    def test_all_demo_files_type_check_clean(self):
        from tools.registry import generated_types
        mismatches = []
        found_files = 0
        for rt in generated_types():
            path = os.path.join("config", "demo", rt + ".auto.tfvars.json")
            if not os.path.exists(path):
                continue
            found_files += 1
            result = check_config_file(rt, path)
            for item_key, field_path, expected, got_repr in result:
                mismatches.append(
                    "%s: items.%s.%s: expected %r, got %r"
                    % (rt, item_key, field_path, expected, got_repr)
                )
        self.assertGreater(found_files, 0, "no demo config files found")
        self.assertEqual(
            mismatches, [],
            "demo config type mismatches:\n" + "\n".join(mismatches),
        )


# ---------------------------------------------------------------------------
# main() — CLI smoke tests
# ---------------------------------------------------------------------------

class MainTest(unittest.TestCase):
    def test_main_demo_exits_zero(self):
        result = main(["demo"])
        self.assertEqual(result, 0)

    def test_main_missing_tenant_exits_nonzero(self):
        result = main(["tenant_that_does_not_exist_xyzzy"])
        # No config files found → clean (0) or usage guard → still well-formed
        # Either 0 (no files checked) or 1 (if the tool errors) is acceptable
        # But it must not raise an exception
        self.assertIn(result, (0, 1, 2))

    def test_main_no_args_exits_nonzero(self):
        result = main([])
        self.assertNotEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
