"""Tests for tools/audit.py - offline parts only (parse/filter/render).

The network flow (async report request/poll/download) is exercised at
work like the fetcher was; everything that consumes its output is
testable here, including degraded shapes.
"""
import io
import json
import sys
import unittest

try:
    from unittest import mock
except ImportError:  # pragma: no cover - 3.6-floor stdlib has unittest.mock
    import mock

from tools import audit
from tools.audit import filter_rows, main, parse_audit_rows, render_attribution

CSV = """\
Time,Action,Category,Sub Category,Resource,Interface,Admin ID,Client IP,Result
"Jun 11, 2026 9:14:02 AM EDT",UPDATE,URL_CATEGORIES,,"ssl_bypass_list",UI,infosec.admin@example.invalid,10.0.0.5,SUCCESS
"Jun 11, 2026 9:20:44 AM EDT",SIGN_IN,AUTHENTICATION,,,UI,someone.else@example.invalid,10.0.0.9,SUCCESS
"""


class ParseTest(unittest.TestCase):
    def test_parses_rows_with_fuzzy_columns(self):
        rows = parse_audit_rows(CSV)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["action"], "UPDATE")
        self.assertEqual(rows[0]["admin"], "infosec.admin@example.invalid")
        self.assertIn("URL_CATEGOR", rows[0]["category"])

    def test_garbage_input_yields_no_rows(self):
        self.assertEqual(parse_audit_rows("not,a,real\nreport"), [])
        self.assertEqual(parse_audit_rows(""), [])


class FilterTest(unittest.TestCase):
    def test_splits_matched_vs_other(self):
        rows = parse_audit_rows(CSV)
        matched, other = filter_rows(rows, ["zia_url_categories"])
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["admin"], "infosec.admin@example.invalid")
        self.assertEqual(len(other), 1)

    def test_unknown_resource_type_matches_nothing(self):
        rows = parse_audit_rows(CSV)
        matched, other = filter_rows(rows, ["zpa_segment_group"])
        self.assertEqual(matched, [])
        self.assertEqual(len(other), 2)


class RenderTest(unittest.TestCase):
    def test_renders_table_and_collapsible_other(self):
        rows = parse_audit_rows(CSV)
        matched, other = filter_rows(rows, ["zia_url_categories"])
        out = render_attribution(matched, other, 24)
        self.assertIn("| Time | Admin |", out)
        self.assertIn("infosec.admin@example.invalid", out)
        self.assertIn("<details>", out)
        out.encode("ascii")

    def test_no_matches_message(self):
        out = render_attribution([], [], 24)
        self.assertIn("No audit entries matched", out)

    def test_other_section_overflow_note(self):
        # >limit "other" changes must announce the truncation, mirroring
        # the matched-table "(+N more matching entries)" note.
        other = [
            {"time": "t%d" % i, "admin": "a@x.invalid",
             "action": "UPDATE", "category": "CAT"}
            for i in range(53)
        ]
        out = render_attribution([], other, 24, limit=50)
        self.assertIn("(+3 more)", out)

    def test_pipe_and_newline_escaped(self):
        # A pipe in a field must not mis-split the table columns; a newline
        # must not break the row across lines.
        matched = [{
            "time": "t", "admin": "ad|min@x.invalid", "action": "UP\nDATE",
            "category": "URL_CATEGORIES", "resource": "name|with|pipes",
        }]
        out = render_attribution(matched, [], 24)
        self.assertIn("ad\\|min@x.invalid", out)
        self.assertIn("name\\|with\\|pipes", out)
        self.assertNotIn("UP\nDATE", out)


class AuditFetchFlowTest(unittest.TestCase):
    class Opener(object):
        def __init__(self, responses):
            self.responses = {}
            for url, queue in responses.items():
                self.responses[url] = list(queue)
            self.calls = []
            self.bodies = []

        def __call__(self, method, url, headers, body):
            self.calls.append((method, url, headers))
            self.bodies.append(body)
            status, payload = self.responses[url].pop(0)
            if isinstance(payload, bytes):
                raw = payload
            elif isinstance(payload, str):
                raw = payload.encode("utf-8")
            else:
                raw = json.dumps(payload).encode("utf-8")
            return status, raw

    def _fetch_with(self, responses):
        opener = self.Opener(responses)
        with mock.patch.object(audit, "real_opener", return_value=opener), \
                mock.patch.object(audit, "acquire_token", return_value="tok"):
            out = audit.fetch_audit_csv({}, 1, sleep=lambda _s: None,
                                        now_ms=3600 * 1000)
        return out, opener

    def test_status_id_from_post_is_used_for_poll_and_download(self):
        csv_text = "Time,Action,Admin ID\n"
        base = "https://api.zsapi.net/zia/api/v1/"
        out, opener = self._fetch_with({
            base + "auditlogEntryReport": [
                (200, {"statusId": "report-123"}),
            ],
            base + "auditlogEntryReport?statusId=report-123": [
                (200, {"status": "COMPLETE"}),
            ],
            base + "auditlogEntryReport/download?statusId=report-123": [
                (200, csv_text),
            ],
        })
        self.assertEqual(out, csv_text)
        self.assertEqual([call[1] for call in opener.calls], [
            base + "auditlogEntryReport",
            base + "auditlogEntryReport?statusId=report-123",
            base + "auditlogEntryReport/download?statusId=report-123",
        ])
        body = json.loads(opener.bodies[0].decode("utf-8"))
        self.assertEqual(body["startTime"], 0)
        self.assertEqual(body["endTime"], 3600 * 1000)

    def test_empty_post_response_keeps_legacy_bare_poll_shape(self):
        csv_text = "Time,Action,Admin ID\n"
        base = "https://api.zsapi.net/zia/api/v1/"
        out, opener = self._fetch_with({
            base + "auditlogEntryReport": [
                (204, b""),
                (200, {"status": "COMPLETE"}),
            ],
            base + "auditlogEntryReport/download": [
                (200, csv_text),
            ],
        })
        self.assertEqual(out, csv_text)
        self.assertEqual([call[1] for call in opener.calls], [
            base + "auditlogEntryReport",
            base + "auditlogEntryReport",
            base + "auditlogEntryReport/download",
        ])


class MainContractTest(unittest.TestCase):
    """main() is strictly advisory: ANY failure degrades to exit 0 with an
    'attribution unavailable' note. These pin that contract - the most
    important behavior in the module and previously untested."""

    def _run_main(self, argv):
        old = sys.stdout
        sys.stdout = io.StringIO()
        try:
            rc = main(argv)
            out = sys.stdout.getvalue()
        finally:
            sys.stdout = old
        return rc, out

    def test_non_integer_hours_degrades_to_exit_0(self):
        # int(argv[1]) lives inside the try now: a bad hours value must
        # NOT crash and gate the drift PR.
        rc, out = self._run_main(["demo", "notanumber"])
        self.assertEqual(rc, 0)
        self.assertIn("unavailable", out)

    def test_fetch_systemexit_degrades_to_exit_0(self):
        # Missing-credential helpers in the fetch plumbing raise SystemExit;
        # it must not escape the advisory contract.
        with mock.patch.object(
                audit, "fetch_audit_csv",
                side_effect=SystemExit("missing env var")):
            rc, out = self._run_main(["demo", "24"])
        self.assertEqual(rc, 0)
        self.assertIn("Audit attribution unavailable", out)

    def test_fetch_runtimeerror_degrades_to_exit_0(self):
        with mock.patch.object(
                audit, "fetch_audit_csv",
                side_effect=RuntimeError("HTTP 403")):
            rc, out = self._run_main(["demo", "24"])
        self.assertEqual(rc, 0)
        self.assertIn("Audit attribution unavailable", out)



class MultilineErrorRenderingTest(unittest.TestCase):
    def test_newlines_in_failure_message_collapse_to_one_line(self):
        # fetch-plumbing hints carry \n; a raw newline breaks the italic
        # span and strands the hint as stray markdown in the PR body
        import io, sys
        import tools.audit as audit_mod

        def boom(environ, hours):
            raise SystemExit("cannot reach host\nhint: set REQUESTS_CA_BUNDLE")

        old_fetch, audit_mod.fetch_audit_csv = audit_mod.fetch_audit_csv, boom
        old_out, sys.stdout = sys.stdout, io.StringIO()
        try:
            code = audit_mod.main(["t", "24", "zia_url_filtering_rules"])
            body = sys.stdout.getvalue()
        finally:
            audit_mod.fetch_audit_csv = old_fetch
            sys.stdout = old_out
        self.assertEqual(code, 0)
        attribution = [l for l in body.splitlines() if "unavailable" in l]
        self.assertEqual(len(attribution), 1)
        self.assertIn("REQUESTS_CA_BUNDLE", attribution[0])


if __name__ == "__main__":
    unittest.main()
