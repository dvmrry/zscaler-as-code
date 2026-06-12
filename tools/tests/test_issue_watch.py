"""Offline tests for the upstream issue watcher (tools/issue_watch.py).

No network: check() takes an injectable fetcher (the bump_check
pattern). The contract under test: only issues mentioning OUR resources
count, only NEW-vs-baseline ones are fatal, and the baseline updates on
demand.
"""
import json
import os
import shutil
import tempfile
import unittest

import tools.issue_watch as iw


def _issue(number, title, body="", state="open", pr=False):
    doc = {"number": number, "title": title, "body": body, "state": state,
           "html_url": "https://github.com/x/y/issues/%d" % number}
    if pr:
        doc["pull_request"] = {"url": "https://api.github.com/x"}
    return doc


class WatchTermsTest(unittest.TestCase):
    def test_terms_carry_full_short_and_spaced_forms(self):
        terms = iw.watch_terms("zpa")
        self.assertIn("zpa_app_connector_group", terms)
        self.assertIn("app_connector_group", terms)
        self.assertIn("app connector group", terms)
        self.assertFalse(any(t.startswith("zia_") for t in terms))


class RelevanceTest(unittest.TestCase):
    def test_title_and_body_matches_count_others_do_not(self):
        terms = iw.watch_terms("zpa")
        hits = iw.relevant_issues([
            _issue(650, "zpa_app_connector_group: missing signingCertId"),
            _issue(700, "question about provider auth", body="my app "
                   "connector group update fails"),
            _issue(710, "docs typo in README"),
        ], terms)
        self.assertEqual([h[0] for h in hits], [650, 700])

    def test_prs_are_labeled(self):
        terms = iw.watch_terms("zpa")
        hits = iw.relevant_issues(
            [_issue(11, "fix segment group drift", pr=True)], terms)
        self.assertEqual(hits[0][1], "PR")


class CheckBaselineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old_baseline = iw.BASELINE_PATH
        iw.BASELINE_PATH = os.path.join(self.tmp, "baseline.json")
        self.old_env = os.environ.pop("UPDATE_BASELINE", None)

    def tearDown(self):
        iw.BASELINE_PATH = self.old_baseline
        if self.old_env is not None:
            os.environ["UPDATE_BASELINE"] = self.old_env
        shutil.rmtree(self.tmp)

    def _fetch(self, repo):
        if repo.endswith("zpa"):
            return [_issue(650, "zpa_app_connector_group: missing "
                                "signingCertId", state="closed")]
        return []

    def test_new_issue_is_fatal_and_marked(self):
        lines, new = iw.check(fetch=self._fetch)
        self.assertEqual(new, 1)
        self.assertTrue(any("NEW" in l and "#650" in l for l in lines))

    def test_baselined_issue_is_quiet(self):
        with open(iw.BASELINE_PATH, "w", encoding="utf-8") as f:
            json.dump({"zscaler/terraform-provider-zpa": [650]}, f)
        lines, new = iw.check(fetch=self._fetch)
        self.assertEqual(new, 0)
        self.assertTrue(any("#650" in l and "NEW" not in l for l in lines))

    def test_update_baseline_writes_union(self):
        with open(iw.BASELINE_PATH, "w", encoding="utf-8") as f:
            json.dump({"zscaler/terraform-provider-zpa": [1]}, f)
        os.environ["UPDATE_BASELINE"] = "1"
        try:
            iw.check(fetch=self._fetch)
        finally:
            del os.environ["UPDATE_BASELINE"]
        with open(iw.BASELINE_PATH, encoding="utf-8") as f:
            doc = json.load(f)
        self.assertEqual(doc["zscaler/terraform-provider-zpa"], [1, 650])


class MainTest(unittest.TestCase):
    def test_usage_rejects_args(self):
        self.assertEqual(iw.main(["x"]), 2)

    def test_network_failure_is_actionable_exit_1(self):
        import io
        import sys

        def boom(repo):
            raise RuntimeError("connection reset")

        old_check, iw.check = iw.check, lambda: (_ for _ in ()).throw(
            RuntimeError("connection reset"))
        old_err, sys.stderr = sys.stderr, io.StringIO()
        try:
            code = iw.main([])
            err = sys.stderr.getvalue()
        finally:
            iw.check = old_check
            sys.stderr = old_err
        self.assertEqual(code, 1)
        self.assertIn("REQUESTS_CA_BUNDLE", err)


if __name__ == "__main__":
    unittest.main()
