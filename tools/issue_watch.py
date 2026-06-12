"""Watch the provider issue trackers for problems with OUR resources —
the other-operators-hit-it-first early-warning lane.

The signingCertId incident (zpa#650) was filed, maintainer-answered,
and closed WEEKS before it reached this pipeline's tenant: the lead
time existed in public and nothing was reading it. This tool fetches
recent issues + PRs from each pinned provider's GitHub repo, keeps the
ones that mention a resource type this repo generates, and compares
them against a committed baseline — exactly the bump-check/mine
notification pattern.

Exit: 0 = nothing new; 4 = NEW relevant issue(s) (make flattens to 2 —
a red scheduled run IS the notification); 1 = network/parse failure.
UPDATE_BASELINE=1 rewrites tools/overrides/issue-watch-baseline.json
after triage. Unauthenticated GitHub API (3 requests/run); proxy and
custom CA via HTTPS_PROXY / REQUESTS_CA_BUNDLE like make fetch.

Stdlib-only, Python 3.6-floor — see AGENTS.md rule 5.
"""
import json
import os
import ssl
import sys

from tools.registry import generated_types

REPOS = {
    "zia": "zscaler/terraform-provider-zia",
    "zpa": "zscaler/terraform-provider-zpa",
    "zcc": "zscaler/terraform-provider-zcc",
}
BASELINE_PATH = os.path.join("tools", "overrides", "issue-watch-baseline.json")
PER_PAGE = 60


def _fetch_issues(repo):
    import urllib.request

    bundle = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    ctx = ssl.create_default_context(cafile=bundle) if bundle else None
    url = ("https://api.github.com/repos/%s/issues"
           "?state=all&sort=updated&direction=desc&per_page=%d"
           % (repo, PER_PAGE))
    req = urllib.request.Request(
        url, headers={"User-Agent": "zscaler-as-code issue-watch",
                      "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def watch_terms(product):
    """The strings that make an issue OURS: the full resource type names
    and their product-stripped short forms (issue titles usually carry
    one or the other, e.g. 'zpa_app_connector_group' or 'app connector
    group' — the spaced form is matched too)."""
    terms = set()
    for rt in generated_types():
        if not rt.startswith(product + "_"):
            continue
        short = rt.split("_", 1)[1]
        terms.add(rt)
        terms.add(short)
        terms.add(short.replace("_", " "))
    return terms


def relevant_issues(issues, terms):
    """(issue dicts, terms) -> [(number, kind, state, title, url)] for
    issues whose title or body mentions one of our resources."""
    out = []
    for issue in issues:
        text = ("%s %s" % (issue.get("title") or "",
                           issue.get("body") or "")).lower()
        if not any(t in text for t in terms):
            continue
        out.append((
            issue.get("number"),
            "PR" if issue.get("pull_request") else "issue",
            issue.get("state") or "?",
            (issue.get("title") or "").strip(),
            issue.get("html_url") or "",
        ))
    return out


def check(fetch=_fetch_issues):
    """-> (lines, new_count). Network errors propagate to main()."""
    baseline = {}
    if os.path.exists(BASELINE_PATH):
        with open(BASELINE_PATH, encoding="utf-8") as f:
            baseline = json.load(f)
    seen_now = {}
    lines = []
    new = 0
    for product in sorted(REPOS):
        repo = REPOS[product]
        terms = watch_terms(product)
        hits = relevant_issues(fetch(repo), terms)
        seen_now[repo] = sorted(set(
            [n for n, _, _, _, _ in hits] + list(baseline.get(repo) or [])))
        known = set(baseline.get(repo) or [])
        for number, kind, state, title, url in hits:
            marker = "" if number in known else " NEW"
            if number not in known:
                new += 1
            lines.append("%s%s %s#%d [%s] %s\n    %s"
                         % (kind.upper(), marker, repo, number, state,
                            title, url))
    if os.environ.get("UPDATE_BASELINE"):
        with open(BASELINE_PATH, "w", encoding="utf-8") as f:
            json.dump(seen_now, f, indent=1, sort_keys=True)
            f.write("\n")
        lines.append("baseline updated: %s" % BASELINE_PATH)
    return lines, new


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if argv:
        sys.stderr.write("usage: python -m tools.issue_watch  "
                         "(make issue-watch)\n")
        return 2
    try:
        lines, new = check()
    except Exception as exc:
        sys.stderr.write(
            "issue watch failed: %s\n"
            "hint: network/proxy? HTTPS_PROXY + REQUESTS_CA_BUNDLE apply, "
            "same as make fetch; unauthenticated GitHub API is "
            "rate-limited per IP (60/hour)\n" % exc)
        return 1
    for line in lines:
        sys.stdout.write(line + "\n")
    sys.stdout.write(
        "\n%d NEW upstream issue(s)/PR(s) mentioning our resources\n" % new)
    if new:
        sys.stdout.write(
            "Read each NEW item — other operators hit problems before we "
            "do (the signingCertId class). Then bless the worklist: "
            "UPDATE_BASELINE=1 make issue-watch and commit the baseline.\n")
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
