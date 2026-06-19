"""Tests for tools/opgate.py -- the gate-artifact helper.

These pin the gate contract: intake is ungated (always pass) but slug-guarded;
resolve passes only on exactly-one-match and blocks (nonzero) on zero/many with
candidates surfaced; validate passes only when both shelled checks exit 0 and
captures a failing tail otherwise; and every artifact is deterministic
(sorted keys, trailing newline, byte-identical across identical runs).

subprocess.run is monkeypatched for validate; resolve runs against a seeded tmp
config (cwd is moved into the tmp tree so the default config_root resolves).
"""
import json
import os
import shutil
import tempfile
import unittest

from tools import opgate
from tools.transform import render_tfvars

SLUG = "2026-06-18-block-reddit"
TENANT = "t"


def _write_config(root, tenant, resource_type, items):
    d = os.path.join(root, "config", tenant)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, resource_type + ".auto.tfvars.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_tfvars(items))
    return path


def _write_json(root, name, obj):
    path = os.path.join(root, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(obj))
    return path


class _FakeProc(object):
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class OpgateTestBase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        self._cwd = os.getcwd()
        self.addCleanup(os.chdir, self._cwd)
        os.chdir(self.root)
        # Seed two URL categories so single/zero/many match cases are reachable.
        _write_config(self.root, TENANT, "zia_url_categories", {
            "blocked_social": {"configured_name": "Blocked Social",
                               "urls": ["reddit.com"]},
            "blocked_news": {"configured_name": "Blocked News",
                             "urls": ["news.example.test"]},
        })

    def _artifact(self, filename):
        path = os.path.join(self.root, "outputs", "operate", SLUG, filename)
        with open(path, encoding="utf-8") as f:
            return f.read()


class IntakeTest(OpgateTestBase):
    def test_intake_writes_pass(self):
        intake = _write_json(self.root, "intake.json", {
            "targets": [{"area": "zia_url_categories", "field": "urls",
                         "display_name": "Blocked Social", "op": "add",
                         "values": ["x.example.test"]}],
        })
        rc = opgate.main(["intake", "--slug", SLUG, "--tenant", TENANT,
                          "--intake-json", intake])
        self.assertEqual(rc, 0)
        text = self._artifact("00-intake.json")
        record = json.loads(text)
        self.assertEqual(record["status"], "pass")
        self.assertEqual(record["phase"], "intake")
        self.assertEqual(len(record["targets"]), 1)
        # sorted keys + trailing newline
        self.assertTrue(text.endswith("\n"))
        self.assertEqual(
            text, json.dumps(record, indent=2, sort_keys=True) + "\n")

    def test_intake_bad_slug_refused(self):
        intake = _write_json(self.root, "intake.json", {"targets": []})
        rc = opgate.main(["intake", "--slug", "nope", "--tenant", TENANT,
                          "--intake-json", intake])
        self.assertEqual(rc, 2)


class ResolveTest(OpgateTestBase):
    def _targets(self, display):
        return _write_json(self.root, "targets.json", {
            "targets": [{"area": "zia_url_categories", "field": "urls",
                         "display_name": display, "op": "add",
                         "values": ["x.example.test"]}],
        })

    def test_resolve_single_match_pass(self):
        tgt = self._targets("Blocked Social")
        rc = opgate.main(["resolve", "--slug", SLUG, "--tenant", TENANT,
                          "--target-json", tgt])
        self.assertEqual(rc, 0)
        record = json.loads(self._artifact("01-resolve.json"))
        self.assertEqual(record["status"], "pass")
        self.assertEqual(record["targets"][0]["config_key"], "blocked_social")
        self.assertEqual(record["blocking_issues"], [])

    def test_resolve_zero_match_blocked(self):
        tgt = self._targets("Nonexistent Category")
        rc = opgate.main(["resolve", "--slug", SLUG, "--tenant", TENANT,
                          "--target-json", tgt])
        self.assertEqual(rc, 1)
        record = json.loads(self._artifact("01-resolve.json"))
        self.assertEqual(record["status"], "blocked")
        self.assertTrue(any("no match" in b for b in record["blocking_issues"]))
        self.assertIsNone(record["targets"][0]["config_key"])

    def test_resolve_many_match_blocked(self):
        # "Blocked" matches both seeded categories (substring, case-insensitive).
        tgt = self._targets("Blocked")
        rc = opgate.main(["resolve", "--slug", SLUG, "--tenant", TENANT,
                          "--target-json", tgt])
        self.assertEqual(rc, 1)
        record = json.loads(self._artifact("01-resolve.json"))
        self.assertEqual(record["status"], "blocked")
        joined = " ".join(record["blocking_issues"])
        self.assertIn("blocked_social", joined)
        self.assertIn("blocked_news", joined)


class ValidateTest(OpgateTestBase):
    def _run(self, returns):
        calls = {"n": 0}

        def fake_run(argv):
            i = calls["n"]
            calls["n"] += 1
            rc, out = returns[i]
            return _FakeProc(rc, stdout=out)

        return opgate.cmd_validate(SLUG, TENANT, runner=fake_run)

    def test_validate_both_pass(self):
        rc = self._run([(0, ""), (0, "")])
        self.assertEqual(rc, 0)
        record = json.loads(self._artifact("03-validate.json"))
        self.assertEqual(record["status"], "pass")
        self.assertEqual([c["exit"] for c in record["checks"]], [0, 0])
        self.assertEqual(record["blocking_issues"], [])

    def test_validate_lint_fail_blocked(self):
        rc = self._run([(0, ""), (2, "lint boom on line 9\n")])
        self.assertEqual(rc, 1)
        record = json.loads(self._artifact("03-validate.json"))
        self.assertEqual(record["status"], "blocked")
        self.assertEqual([c["exit"] for c in record["checks"]], [0, 2])
        joined = " ".join(record["blocking_issues"])
        self.assertIn("lint boom on line 9", joined)


class DeterminismTest(OpgateTestBase):
    def test_artifact_is_deterministic(self):
        intake = _write_json(self.root, "intake.json", {
            "targets": [{"area": "zia_url_categories", "field": "urls",
                         "display_name": "Blocked Social", "op": "add",
                         "values": ["x.example.test"]}],
        })
        opgate.main(["intake", "--slug", SLUG, "--tenant", TENANT,
                     "--intake-json", intake])
        first = self._artifact("00-intake.json")
        opgate.main(["intake", "--slug", SLUG, "--tenant", TENANT,
                     "--intake-json", intake])
        second = self._artifact("00-intake.json")
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
