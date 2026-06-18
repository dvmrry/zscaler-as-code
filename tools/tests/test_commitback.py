"""Tests for the commit-back script (pipelines/commitback.sh).

The script is the pulled home for commit-back logic - every field
incident (az hangs, set -e killing the PR call after the push, PR
pileup) happened in adapted inline YAML, so its behavior is pinned here
like any tool. Runs against a scratch repo with a local bare origin and
a PATH-stubbed curl: the full flow (detect changed types -> snapshot ->
one stable branch + PR per type) executes with zero network.
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

# Stub curl: append each call's URL to CURL_LOG and each POST body to
# CURL_BODIES (one JSON object per line), write a canned PR response, and
# print the configured HTTP status as curl's -w '%{http_code}' output.
STUB_CURL = r"""#!/bin/sh
out=""; body=""; prev=""; url=""
for a in "$@"; do
  case "$prev" in
    -o) out="$a";;
    -d) body="$a";;
  esac
  url="$a"; prev="$a"
done
printf '%s\n' "$url" >> "$CURL_LOG"
if [ -n "$body" ]; then cat "${body#@}" >> "$CURL_BODIES"; printf '\n' >> "$CURL_BODIES"; fi
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
            "CURL_BODIES": os.path.join(self.tmp, "curl.bodies"),
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

    def _stage_types(self, *types):
        """Write a config + imports file for each resource type, mimicking a
        fresh transform; they are new files vs main (= additions to detect)."""
        for d in ("config/t1", "imports/t1"):
            os.makedirs(os.path.join(self.work, d), exist_ok=True)
        for rt in types:
            with open(os.path.join(self.work, "config/t1", rt + ".auto.tfvars.json"),
                      "w", encoding="utf-8") as f:
                f.write('{"items": {}}\n')
            with open(os.path.join(self.work, "imports/t1", rt + "_imports.tf"),
                      "w", encoding="utf-8") as f:
                f.write("# import blocks for %s\n" % rt)

    def _write_report(self, body):
        path = os.path.join(self.work, "reports/t1/drift.md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        return "reports/t1/drift.md"

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
        return [b for b in out.decode("utf-8").split() if b != "main"]

    def _pr_bodies(self):
        path = self.env["CURL_BODIES"]
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def test_one_stable_branch_and_pr_per_changed_type(self):
        self._stage_types("zia_url_categories", "zpa_segment_group")
        code, out = self._run()
        self.assertEqual(code, 0, out)
        self.assertEqual(sorted(self._origin_branches()),
                         ["bootstrap/t1/zia_url_categories",
                          "bootstrap/t1/zpa_segment_group"], out)
        bodies = self._pr_bodies()
        self.assertEqual(len(bodies), 2, out)  # one PR POST per type
        by_src = {b["sourceRefName"]: b for b in bodies}
        self.assertEqual(
            by_src["refs/heads/bootstrap/t1/zia_url_categories"]["title"],
            "bootstrap refresh: t1 - zia_url_categories")
        for b in bodies:
            self.assertEqual(b["targetRefName"], "refs/heads/main")
        # project space survived as %20 in the REST URL
        with open(self.env["CURL_LOG"], encoding="utf-8") as f:
            self.assertIn("My%20Project", f.read())

    def test_branch_names_are_stable_no_timestamp(self):
        self._stage_types("zia_rule_labels")
        code, out = self._run()
        self.assertEqual(code, 0, out)
        # exact stable name - nothing appended (no date), so a re-drift
        # refreshes this same branch instead of stacking a new one
        self.assertEqual(self._origin_branches(), ["bootstrap/t1/zia_rule_labels"])

    def test_rerun_refreshes_same_branches_not_duplicates(self):
        self._stage_types("zia_url_categories", "zpa_segment_group")
        self._run()
        self._stage_types("zia_url_categories", "zpa_segment_group")  # workspace is ephemeral
        code, out = self._run()
        self.assertEqual(code, 0, out)
        # still exactly two branches - stable names, force-pushed, not doubled
        self.assertEqual(len(self._origin_branches()), 2, out)

    def test_409_is_success_already_open(self):
        # a stable branch whose PR is already open: the push refreshed it,
        # and ADO 409s the create - that is success, not failure
        self._stage_types("zia_url_categories")
        code, out = self._run(CURL_STATUS="409")
        self.assertEqual(code, 0, out)
        self.assertIn("active PR already open", out)
        self.assertEqual(self._origin_branches(), ["bootstrap/t1/zia_url_categories"])

    def test_server_error_fails_loud_after_push(self):
        self._stage_types("zia_url_categories")
        code, out = self._run(CURL_STATUS="500")
        self.assertEqual(code, 1, out)
        self.assertIn("HTTP 500", out)
        # the branch is pushed before the PR call, so it survives for a re-run
        self.assertEqual(self._origin_branches(), ["bootstrap/t1/zia_url_categories"])

    def test_artifact_note_in_every_body(self):
        self._stage_types("zia_url_categories", "zpa_segment_group")
        code, out = self._run(ARTIFACT_NOTE="_full report: drift-report_")
        self.assertEqual(code, 0, out)
        bodies = self._pr_bodies()
        self.assertEqual(len(bodies), 2)
        for b in bodies:
            self.assertIn("_full report: drift-report_", b["description"])
            self.assertIn("resource type", b["description"])

    def test_pr_body_file_replaces_one_liner_in_every_body(self):
        self._stage_types("zia_url_categories", "zpa_segment_group")
        report = "## Drift report\n\n| resource | count |\n|---|---|\n"
        body_file = self._write_report(report)
        code, out = self._run(PR_BODY_FILE=body_file)
        self.assertEqual(code, 0, out)
        bodies = self._pr_bodies()
        self.assertEqual(len(bodies), 2)
        for b in bodies:
            self.assertEqual(b["description"], report)
            self.assertNotIn("Automated bootstrap", b["description"])

    def test_pr_body_file_can_still_append_artifact_note(self):
        self._stage_types("zia_url_categories")
        body_file = self._write_report("## Drift report\n")
        code, out = self._run(PR_BODY_FILE=body_file,
                              ARTIFACT_NOTE="_also in artifact_")
        self.assertEqual(code, 0, out)
        self.assertEqual(
            self._pr_bodies()[0]["description"],
            "## Drift report\n\n_also in artifact_\n",
        )

    def test_pr_body_file_is_ascii_normalized(self):
        self._stage_types("zia_url_categories")
        body_file = self._write_report(
            "## Drift \u2014 report\n"
            "- **\u2212 item** \u2014 changed\u2026 \u201cquoted\u201d\n"
        )
        code, out = self._run(PR_BODY_FILE=body_file)
        self.assertEqual(code, 0, out)
        desc = self._pr_bodies()[0]["description"]
        self.assertEqual(
            desc,
            '## Drift -- report\n- **- item** -- changed... "quoted"\n',
        )
        desc.encode("ascii")

    def test_missing_pr_body_file_fails_before_push(self):
        self._stage_types("zia_url_categories")
        code, out = self._run(PR_BODY_FILE="reports/t1/missing.md")
        self.assertEqual(code, 2, out)
        self.assertIn("PR_BODY_FILE", out)
        self.assertEqual(self._origin_branches(), [])

    def test_clean_worktree_is_a_quiet_success(self):
        code, out = self._run()
        self.assertEqual(code, 0, out)
        self.assertIn("nothing to commit", out)
        self.assertEqual(self._origin_branches(), [])
        self.assertFalse(os.path.exists(self.env["CURL_LOG"]))

    def test_unrelated_change_is_not_a_resource_type(self):
        # a change outside the <type>.auto.tfvars.json / <type>_imports.tf
        # shape derives no type -> nothing to commit
        os.makedirs(os.path.join(self.work, "config/t1"), exist_ok=True)
        with open(os.path.join(self.work, "config/t1", "README.md"),
                  "w", encoding="utf-8") as f:
            f.write("notes\n")
        code, out = self._run()
        self.assertEqual(code, 0, out)
        self.assertIn("nothing to commit", out)
        self.assertEqual(self._origin_branches(), [])

    def _commit_types_to_main(self, *types):
        """Commit + push config/imports for types to main, so the PR base
        (origin/main) already tracks them - needed to exercise deletions."""
        self._stage_types(*types)
        self._git("-C", self.work, "add", "-A")
        self._git("-C", self.work, "-c", "user.name=t", "-c", "user.email=t@invalid",
                  "commit", "-q", "-m", "seed types")
        self._git("-C", self.work, "push", "-q", "origin", "main")

    def test_deletion_propagates_to_its_branch(self):
        # a resource dropped upstream: its file is removed, not rewritten -
        # the per-type branch must carry the removal, not silently skip it
        self._commit_types_to_main("zia_url_categories")
        os.remove(os.path.join(self.work, "config/t1/zia_url_categories.auto.tfvars.json"))
        os.remove(os.path.join(self.work, "imports/t1/zia_url_categories_imports.tf"))
        code, out = self._run()
        self.assertEqual(code, 0, out)
        self.assertEqual(self._origin_branches(), ["bootstrap/t1/zia_url_categories"], out)
        tree = subprocess.check_output(
            ["git", "-C", self.origin, "ls-tree", "-r", "--name-only",
             "bootstrap/t1/zia_url_categories"]).decode("utf-8")
        self.assertNotIn("config/t1/zia_url_categories.auto.tfvars.json", tree)
        self.assertNotIn("imports/t1/zia_url_categories_imports.tf", tree)

    def test_config_only_first_bootstrap_no_imports_dir(self):
        # first bootstrap of a type with no import blocks: config/t1 exists,
        # imports/t1 does not - `git add` must not 128 on the missing dir
        os.makedirs(os.path.join(self.work, "config/t1"), exist_ok=True)
        with open(os.path.join(self.work, "config/t1", "zia_rule_labels.auto.tfvars.json"),
                  "w", encoding="utf-8") as f:
            f.write('{"items": {}}\n')
        self.assertFalse(os.path.exists(os.path.join(self.work, "imports/t1")))
        code, out = self._run()
        self.assertEqual(code, 0, out)
        self.assertEqual(self._origin_branches(), ["bootstrap/t1/zia_rule_labels"], out)

    def test_lookup_sidecar_is_committed_with_its_resource_type(self):
        self._stage_types("zia_url_categories")
        with open(os.path.join(self.work, "config/t1/zia_url_categories.lookup.json"),
                  "w", encoding="utf-8") as f:
            f.write('{"CUSTOM_01": "Readable"}\n')

        code, out = self._run()
        self.assertEqual(code, 0, out)

        tree = subprocess.check_output(
            ["git", "-C", self.origin, "ls-tree", "-r", "--name-only",
             "bootstrap/t1/zia_url_categories"]).decode("utf-8")
        self.assertIn("config/t1/zia_url_categories.lookup.json", tree)

    def test_lookup_only_change_detects_its_resource_type(self):
        self._stage_types("zia_url_categories")
        with open(os.path.join(self.work, "config/t1/zia_url_categories.lookup.json"),
                  "w", encoding="utf-8") as f:
            f.write('{"CUSTOM_01": "Old"}\n')
        self._git("-C", self.work, "add", "-A")
        self._git("-C", self.work, "-c", "user.name=t", "-c", "user.email=t@invalid",
                  "commit", "-q", "-m", "seed lookup")
        self._git("-C", self.work, "push", "-q", "origin", "main")

        with open(os.path.join(self.work, "config/t1/zia_url_categories.lookup.json"),
                  "w", encoding="utf-8") as f:
            f.write('{"CUSTOM_01": "New"}\n')

        code, out = self._run()
        self.assertEqual(code, 0, out)
        self.assertEqual(self._origin_branches(), ["bootstrap/t1/zia_url_categories"], out)
        blob = subprocess.check_output(
            ["git", "-C", self.origin, "show",
             "bootstrap/t1/zia_url_categories:config/t1/zia_url_categories.lookup.json"]
        ).decode("utf-8")
        self.assertEqual(blob, '{"CUSTOM_01": "New"}\n')

    def test_missing_token_names_the_yaml_mapping(self):
        self._stage_types("zia_url_categories")
        env = dict(self.env)
        del env["SYSTEM_ACCESSTOKEN"]
        proc = subprocess.Popen(
            ["bash", SCRIPT], cwd=self.work, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out = proc.communicate()[0].decode("utf-8", "replace")
        self.assertEqual(proc.returncode, 2, out)
        self.assertIn("SYSTEM_ACCESSTOKEN: $(System.AccessToken)", out)
        self.assertEqual(self._origin_branches(), [])  # refuses before any push

    def test_charset_validated(self):
        self._stage_types("zia_url_categories")
        for bad in ({"TENANT": "t1; rm -rf /"}, {"BRANCH_PREFIX": "a b"}):
            code, out = self._run(**bad)
            self.assertEqual(code, 2, out)
            self.assertIn("must match", out)

    def test_proxy_announced_without_leaking_credentials(self):
        self._stage_types("zia_url_categories")
        code, out = self._run(HTTPS_PROXY="http://u:sekrit@proxy.test:8080")
        self.assertEqual(code, 0, out)
        self.assertIn("https proxy: set", out)
        self.assertNotIn("sekrit", out)

    def test_direct_egress_announced(self):
        self._stage_types("zia_url_categories")
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
