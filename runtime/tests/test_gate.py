"""Tests for zac_runtime.gate -- the modern gate runner.

These pin the artifact contract: pass -> status "pass" with no tail; nonzero ->
status "blocked" with a tail; the recorded command is `make <gate> <vars...>`;
and two identical runs are byte-identical (determinism). subprocess.run is
monkeypatched so the suite never actually shells make.
"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zac_runtime import gate


def _fake_completed(returncode, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=["make"], returncode=returncode, stdout=stdout, stderr=stderr,
    )


class RunGateTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def _run(self, returncode, gate_name="typecheck", make_vars=None,
             stdout="", stderr=""):
        make_vars = make_vars or []
        with mock.patch.object(
            gate.subprocess, "run",
            return_value=_fake_completed(returncode, stdout, stderr),
        ):
            rc = gate.run_gate(gate_name, make_vars, self.dir)
        path = self.dir / ("%s.status.json" % gate_name)
        return rc, path

    def test_pass_status_no_tail(self):
        rc, path = self._run(0)
        self.assertEqual(rc, 0)
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "pass")
        self.assertNotIn("tail", record)
        self.assertEqual(record["returncode"], 0)

    def test_blocked_status_has_tail(self):
        rc, path = self._run(2, stdout="line one\nline two\nboom\n")
        self.assertEqual(rc, 1)
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "blocked")
        self.assertIn("tail", record)
        self.assertIn("boom", record["tail"])

    def test_command_is_make_gate_vars(self):
        _, path = self._run(0, gate_name="lint", make_vars=["TENANT=demo"])
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(record["command"], "make lint TENANT=demo")

    def test_artifact_sorted_keys_trailing_newline(self):
        _, path = self._run(0)
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.endswith("\n"))
        record = json.loads(text)
        self.assertEqual(
            text, json.dumps(record, indent=2, sort_keys=True) + "\n")

    def test_non_allowlisted_gate_refused(self):
        # Least-privilege: refuse state-mutating targets so `make gate
        # GATE=apply` can never reach a live terraform apply. The refusal is
        # raised before any subprocess call, so no make is shelled.
        for bad in ("apply", "import-one", "statefill", "fetch", "commitback",
                    "mine"):
            with self.assertRaises(ValueError):
                gate.run_gate(bad, [], self.dir)

    def test_bare_target_in_make_vars_refused(self):
        # A bare word in make_vars would run as an extra `make` goal
        # (make typecheck apply), bypassing the allowlist -- refuse it.
        for bad_vars in (["apply"], ["TENANT=demo", "apply"],
                         ["apply", "ALLOW_NON_MAIN=1"]):
            with self.assertRaises(ValueError):
                gate.run_gate("typecheck", bad_vars, self.dir)

    def test_traversal_gate_refused(self):
        with self.assertRaises(ValueError):
            gate.run_gate("../oops", [], self.dir)

    def test_two_runs_byte_identical(self):
        # Run the SAME gate twice into two dirs so the gate-derived parts match.
        dir_b = Path(tempfile.mkdtemp())
        with mock.patch.object(
            gate.subprocess, "run",
            return_value=_fake_completed(2, "same\n", ""),
        ):
            gate.run_gate("typecheck", [], self.dir)
            gate.run_gate("typecheck", [], dir_b)
        a = (self.dir / "typecheck.status.json").read_text(encoding="utf-8")
        b = (dir_b / "typecheck.status.json").read_text(encoding="utf-8")
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
