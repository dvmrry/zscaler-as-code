"""Pins the deployment-overlay contract (docs/extending.md).

A top-level deployment overlay directory must be invisible to every template
gate, because the gates are path-scoped to the template's own directories. If a
future change makes a gate scan the repo root, the first test here fails -- that
is the point: it turns the convention into an enforced guarantee.

Stdlib only, Python 3.6 floor.
"""
import os
import shutil
import subprocess
import unittest

_REPO = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PROBE = "_overlay_tolerance_probe"

# The exact path-scoped git-status pathspecs the gates use (see the Makefile):
# generate CHECK=1, check-demo (demo tenant), check-envs. A top-level overlay
# directory must appear in NONE of them.
_GATE_SCOPES = [
    ["schemas/provider"],             # make generate CHECK=1 (schemas)
    ["modules", "schemas/tfvars"],    # make generate CHECK=1 (modules/tfvars)
    ["config/demo", "imports/demo"],  # make check-demo
    ["envs"],                         # make check-envs
]


def _porcelain(scope):
    return subprocess.check_output(
        ["git", "status", "--porcelain", "--"] + scope,
        cwd=_REPO, universal_newlines=True)


class DeploymentOverlayToleranceTest(unittest.TestCase):
    def setUp(self):
        # An UN-ignored top-level dir, so it would show in an unscoped
        # `git status` -- the test proves the gates' scoping excludes it.
        self.probe = os.path.join(_REPO, _PROBE)
        os.makedirs(self.probe)
        with open(os.path.join(self.probe, "data.json"), "w",
                  encoding="utf-8") as f:
            f.write("{}\n")
        self.addCleanup(shutil.rmtree, self.probe, True)

    def test_overlay_absent_from_every_gate_scope(self):
        for scope in _GATE_SCOPES:
            self.assertNotIn(
                _PROBE, _porcelain(scope),
                "an overlay dir leaked into a path-scoped gate (scope=%r); a "
                "gate is no longer path-scoped to template dirs" % scope)

    def test_default_overlay_dir_is_gitignored(self):
        # _local/ (the OVERLAY default) is ignored by the template .gitignore.
        rc = subprocess.call(
            ["git", "check-ignore", "-q", "_local/x"], cwd=_REPO)
        self.assertEqual(
            rc, 0, "_local/ should be gitignored by the template .gitignore")


if __name__ == "__main__":
    unittest.main()
