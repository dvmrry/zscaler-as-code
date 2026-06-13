"""Tests for the commit-back script (pipelines/commitback.sh).

The script is the pulled home for commit-back logic — every field
incident (az hangs, set -e killing the PR call after the push) happened
in adapted inline YAML, so its behavior is pinned here like any tool.
Runs against a scratch repo with a local bare origin and a PATH-stubbed
curl: the full flow (detect -> branch -> commit -> push -> PR REST
call) executes with zero network.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(REPO_ROOT, "pipelines", "commitback.sh")

STUB_CURL = """#!/bin/sh
prev=""
out=""
body=""
printf '%s\\n' "$@" > "$CURL_LOG"
for a in "$@"; do
  case "$prev" in
    -o) out="$a";;
    -d) body="$a";;
  esac
  prev="$a"
done
if [ -n "$body" ]; then cp "${body#@}" "$CURL_BODY"; fi
if [ -n "$out" ]; then echo '{"pullRequestId": 1}' > "$out"; fi
printf '%s' "${CURL_STATUS:-201}"
"""

HAVE_TOOLS = shutil.which("git") and shutil.which("bash")


@unittest.skipUnless(HAVE_TOOLS, "git and bash required")
class CommitbackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="commitback-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.work = os.path.join(self.tmp, "work")
        self.origin = os.path.join(self.tmp, "origin.git")
        stub_bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.work)
        os.makedirs(stub_bin)
        curl = os.path.join(stub_bin, "curl")
        with open(curl, "w", encoding="utf-8") as f:
            f.write(STUB_CURL)
        os.chmod(curl, 0o755)

        self._git("init", "--bare", self.origin)
        self._git("init", self.work)
        self._git("-C", self.work, "checkout", "-q", "-b", "main")
        with open(os.path.join(self.work, "seed.txt"), "w",
                  encoding="utf-8") as f:
            f.write("seed\n")
        self._git("-C", self.work, "add", "seed.txt")
        self._git("-C", self.work, "-c", "user.name=t",
                  "-c", "user.email=t@invalid", "commit", "-q", "-m", "seed")
        self._git("-C", self.work, "remote", "add", "origin", self.origin)
        self._git("-C", self.work, "push", "-q", "-u", "origin", "main")

        self.env = dict(os.environ)
        self.env.update({
            "PATH": stub_bin + os.pathsep, "CURL_LOG": os.path.join(self.tmp, "curl.log"),
            "CURL_BODY": os.path.join(self.tmp, "curl.body"),
            "TENANT": "t1", "BRANCH_PREFIX": "bootstrap",
            "PR_TITLE": "bootstrap refresh: t1",
            "SYSTEM_ACCESSTOKEN": "fake-token",
            "SYSTEM_COLLECTIONURI": "https://dev.example.test/org/",
            "SYSTEM_TEAMPROJECT": "My Project",  # space pins URL encoding
            "BUILD_REPOSITORY_ID": "repo-guid",
            "GIT_CONFIG_NOSYSTEM": "1",
        })
        self.env["PATH"] += os.environ.get("PATH", "")

    def _git(self, *args):
        subprocess.check_call(("git",) + args, stdout=subprocess.DEVNULL,
                              stderr=subprocess.STDOUT)

    def _stage_change(self):
        for d in ("config/t1", "imports/t1"):
            path = os.path.join(self.work, d)
            os.makedirs(path)
            with open(os.path.join(path, "x.json"), "w",
                      encoding="utf-8") as f:
                f.write("{}\n")

    def _run(self, **env_extra):
        env = dict(self.env)
        env.update(env_extra)
        proc = subprocess.Popen(
            ["bash", SCRIPT], cwd=self.work, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out = proc.communicate()[0].decode("utf-8", "replace")
        return proc.returncode, out

    def _origin_branches(self):
        out = subprocess.check_output(
            ["git", "-C", self.origin, "branch", "--list",
             "--format=%(refname:short)"])
        return out.decode("utf-8").split()

    def test_happy_path_pushes_branch_and_opens_pr(self):
        self._stage_change()
        code, out = self._run()
        self.assertEqual(code, 0, out)
        branches = [b for b in self._origin_branches()
                    if b.startswith("bootstrap/t1/")]
        self.assertEqual(len(branches), 1, out)
        with open(self.env["CURL_BODY"], encoding="utf-8") as f:
            body = json.load(f)
        self.assertEqual(body["sourceRefName"], "refs/heads/" + branches[0])
        self.assertEqual(body["targetRefName"], "refs/heads/main")
        self.assertEqual(body["title"], "bootstrap refresh: t1")
        self.assertTrue(body["description"].startswith(
            "Automated commit-back"))
        with open(self.env["CURL_LOG"], encoding="utf-8") as f:
            curl_args = f.read()
        self.assertIn("My%20Project", curl_args)  # space survived, encoded
        self.assertIn("[commit-back 5/5] PR opened", out)

    def test_clean_worktree_is_a_quiet_success(self):
        code, out = self._run()
        self.assertEqual(code, 0, out)
        self.assertIn("nothing to commit", out)
        self.assertEqual([b for b in self._origin_branches()
                          if b != "main"], [])
        self.assertFalse(os.path.exists(self.env["CURL_LOG"]))

    def test_missing_token_names_the_yaml_mapping(self):
        self._stage_change()
        env = dict(self.env)
        del env["SYSTEM_ACCESSTOKEN"]
        proc = subprocess.Popen(
            ["bash", SCRIPT], cwd=self.work, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out = proc.communicate()[0].decode("utf-8", "replace")
        self.assertEqual(proc.returncode, 2, out)
        self.assertIn("SYSTEM_ACCESSTOKEN: $(System.AccessToken)", out)
        # preflight refuses BEFORE any git mutation
        self.assertEqual([b for b in self._origin_branches()
                          if b != "main"], [])

    def test_missing_description_file_is_never_fatal(self):
        self._stage_change()
        code, out = self._run(DESCRIPTION_FILE="reports/t1/nope.md",
                              ARTIFACT_NOTE="_artifact: x_")
        self.assertEqual(code, 0, out)
        with open(self.env["CURL_BODY"], encoding="utf-8") as f:
            body = json.load(f)
        self.assertIn("report missing from workspace", body["description"])
        self.assertIn("_artifact: x_", body["description"])

    def test_http_error_fails_loud_after_push(self):
        self._stage_change()
        code, out = self._run(CURL_STATUS="409")
        self.assertEqual(code, 1, out)
        self.assertIn("HTTP 409", out)
        self.assertIn("IS pushed", out)  # the branch survived for a re-run
        self.assertEqual(len([b for b in self._origin_branches()
                              if b.startswith("bootstrap/t1/")]), 1)

    def test_tenant_charset_is_validated(self):
        self._stage_change()
        code, out = self._run(TENANT="t1; rm -rf /")
        self.assertEqual(code, 2, out)
        self.assertIn("must match", out)

    def test_proxy_announced_without_leaking_credentials(self):
        # field-hit: agent-level git proxy config got the push through,
        # then the REST call hung — the script must surface proxy state,
        # but proxy URLs can carry credentials, so never the value
        self._stage_change()
        code, out = self._run(HTTPS_PROXY="http://u:sekrit@proxy.test:8080")
        self.assertEqual(code, 0, out)
        self.assertIn("https proxy: set", out)
        self.assertNotIn("sekrit", out)

    def test_direct_egress_announced(self):
        self._stage_change()
        env = dict(self.env)
        env.pop("HTTPS_PROXY", None)
        env.pop("https_proxy", None)
        proc = subprocess.Popen(
            ["bash", SCRIPT], cwd=self.work, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out = proc.communicate()[0].decode("utf-8", "replace")
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("https proxy: unset", out)


if __name__ == "__main__":
    unittest.main()
