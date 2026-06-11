"""Best-effort ZIA audit-trail attribution for drift PRs.

Answers "WHO changed this" without chasing people down: pulls the ZIA
audit log for a recent window and emits a markdown table of admin
actions, filtered to the resource types that drifted. Appended to the
drift PR body by the pipeline.

STRICTLY ADVISORY: this must never gate a drift PR. Any failure (no
entitlement, report timeout, unexpected CSV shape) degrades to a
one-line "attribution unavailable" note and exit 0. The ZIA endpoint
is an async report (request -> poll -> download CSV) and only one
report can generate at a time per org — another reason it stays
best-effort.

Usage: python -m tools.audit <tenant> <hours> [resource_type ...]
(creds via the same env vars as make fetch; tenant label is opaque)

Stdlib-only, Python 3.6-floor — see AGENTS.md rule 5.
"""
import csv
import io
import json
import os
import sys
import time

from tools.fetch import (
    acquire_token,
    auth_mode_from_env,
    build_headers,
    compose_url,
    real_opener,
    _require,
)

# resource type -> case-insensitive keywords matched against the audit
# row's category/subcategory/resource columns. Advisory keywords — rows
# matching nothing still appear under "other admin changes".
CATEGORY_KEYWORDS = {
    "zia_url_categories": ("URL_CATEGOR", "URL CATEGOR"),
    "zia_url_filtering_rules": ("URL_FILTER", "URL FILTER"),
    "zia_cloud_app_control_rule": ("CLOUD_APP", "CLOUD APP"),
    "zia_ssl_inspection_rules": ("SSL",),
    "zia_location_management": ("LOCATION",),
    "zia_rule_labels": ("LABEL",),
}

_POLL_SECONDS = 5
_POLL_LIMIT = 24  # ~2 minutes


def _zia(opener, method, auth_mode, ctx, token, path, body=None):
    url = compose_url(auth_mode, "zia", path, ctx)
    headers = build_headers(token)
    if body is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(body).encode()
    status, raw = opener(method, url, headers, body)
    if status >= 300:
        raise RuntimeError("%s %s -> HTTP %d" % (method, path, status))
    return raw


def fetch_audit_csv(env, hours, sleep=time.sleep, now_ms=None):
    """Request, poll, and download the audit report. Returns CSV text."""
    auth_mode = auth_mode_from_env(env)
    opener = real_opener()
    ctx = {
        "cloud": env.get("ZIA_CLOUD", "") or env.get("ZSCALER_CLOUD", ""),
        "customer_id": env.get("ZPA_CUSTOMER_ID", ""),
        "zcc_cloud": env.get("ZCC_CLOUD", ""),
    }
    token = acquire_token(auth_mode, "zia", env, ctx, opener)
    end_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    start_ms = end_ms - hours * 3600 * 1000
    _zia(opener, "POST", auth_mode, ctx, token, "auditlogEntryReport",
         {"startTime": start_ms, "endTime": end_ms})
    for _ in range(_POLL_LIMIT):
        sleep(_POLL_SECONDS)
        raw = _zia(opener, "GET", auth_mode, ctx, token, "auditlogEntryReport")
        status = json.loads(raw.decode("utf-8")).get("status", "")
        if status == "COMPLETE":
            break
        if status in ("ERRORED", "FAILED"):
            raise RuntimeError("audit report status %s" % status)
    else:
        raise RuntimeError("audit report did not complete in time")
    raw = _zia(opener, "GET", auth_mode, ctx, token, "auditlogEntryReport/download")
    return raw.decode("utf-8", "replace")


def _find_column(header, *needles):
    for i, name in enumerate(header):
        low = name.strip().lower()
        for needle in needles:
            if needle in low:
                return i
    return None


def parse_audit_rows(csv_text):
    """Defensive CSV parse -> list of dicts with time/admin/action/what.

    The report ships extra preamble/columns across versions; columns are
    located by fuzzy header match and missing ones degrade to ''.
    """
    rows = []
    reader = csv.reader(io.StringIO(csv_text))
    header = None
    cols = {}
    for record in reader:
        if not record or not any(cell.strip() for cell in record):
            continue
        if header is None:
            lowered = ",".join(record).lower()
            if "action" in lowered and ("admin" in lowered or "time" in lowered):
                header = record
                cols = {
                    "time": _find_column(record, "time"),
                    "admin": _find_column(record, "admin"),
                    "action": _find_column(record, "action"),
                    "category": _find_column(record, "category"),
                    "resource": _find_column(record, "resource"),
                    "result": _find_column(record, "result"),
                }
            continue

        def cell(name):
            i = cols.get(name)
            return record[i].strip() if i is not None and i < len(record) else ""

        rows.append({
            "time": cell("time"),
            "admin": cell("admin"),
            "action": cell("action"),
            "category": cell("category"),
            "resource": cell("resource"),
            "result": cell("result"),
        })
    return rows


def filter_rows(rows, resource_types):
    """Split rows into (matched-to-drifted-types, other-config-changes)."""
    keywords = []
    for rt in resource_types:
        keywords.extend(CATEGORY_KEYWORDS.get(rt, ()))
    matched, other = [], []
    for row in rows:
        haystack = ("%s %s" % (row["category"], row["resource"])).upper()
        if any(k.upper() in haystack for k in keywords):
            matched.append(row)
        else:
            other.append(row)
    return matched, other


def render_attribution(matched, other, hours, limit=50):
    lines = ["## Who changed it (ZIA audit trail, last %dh)" % hours, ""]
    if matched:
        lines.append("| Time | Admin | Action | Category | Resource |")
        lines.append("|---|---|---|---|---|")
        for row in matched[:limit]:
            lines.append("| %s | %s | %s | %s | %s |" % (
                row["time"], row["admin"], row["action"],
                row["category"], row["resource"]))
        if len(matched) > limit:
            lines.append("")
            lines.append("(+%d more matching entries)" % (len(matched) - limit))
    else:
        lines.append(
            "No audit entries matched the drifted resource types in the "
            "window — the change may be older than %dh." % hours)
    if other:
        lines.append("")
        lines.append(
            "<details><summary>%d other admin change(s) in the window"
            "</summary>" % len(other))
        lines.append("")
        for row in other[:limit]:
            lines.append("- %s — %s: %s (%s)" % (
                row["time"], row["admin"], row["action"], row["category"]))
        lines.append("</details>")
    return "\n".join(lines) + "\n"


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) < 2:
        sys.stderr.write(
            "usage: python -m tools.audit <tenant> <hours> [resource_type ...]\n")
        return 2
    hours = int(argv[1])
    resource_types = argv[2:] or sorted(CATEGORY_KEYWORDS)
    try:
        csv_text = fetch_audit_csv(os.environ, hours)
        rows = parse_audit_rows(csv_text)
        matched, other = filter_rows(rows, resource_types)
        sys.stdout.write(render_attribution(matched, other, hours))
    except (Exception, SystemExit) as exc:
        # Advisory only — the drift PR proceeds without attribution.
        # SystemExit included: missing-credential helpers in the fetch
        # plumbing raise it, and it must not escape this contract.
        sys.stdout.write(
            "## Who changed it\n\n_Audit attribution unavailable: %s_\n" % exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
