#!/usr/bin/env bash
# Commit-back: push generated config/imports to a branch and open an
# Azure DevOps PR via REST. Called by the bootstrap/drift pipelines —
# the YAML stays a thin caller so this logic updates on repo pull
# (adapted pipeline YAML does not; field-hit three times).
#
# Required env:
#   TENANT              tenant label ([A-Za-z0-9_.-]+)
#   BRANCH_PREFIX       branch namespace, e.g. bootstrap or drift
#   PR_TITLE            PR title (also the commit message)
#   SYSTEM_ACCESSTOKEN  map EXPLICITLY in the YAML step:
#                         env: { SYSTEM_ACCESSTOKEN: $(System.AccessToken) }
#                       (ADO exposes the other SYSTEM_*/BUILD_* vars
#                       automatically; the token it does not)
# Optional env:
#   COMMIT_PATHS        paths to commit (default: config/<T> imports/<T>)
#   TARGET_BRANCH       PR target (default: main)
#   DESCRIPTION_FILE    file for the PR body, truncated to the ADO 4,000
#                       char server limit; missing file is NEVER fatal
#                       (field-hit: set -e + a dead report file killed an
#                       adapted block after push, before the PR call)
#   ARTIFACT_NOTE       extra line appended to the PR body
#   HTTPS_PROXY         declare on the step if egress rides a proxy:
#                       curl reads ONLY the env — agent-level git proxy
#                       config gets the push through and then the REST
#                       call hangs (field-hit)
#
# ADO setup: the build service identity needs "Contribute" on the repo
# (covers both the branch push and PR creation), and the checkout step
# needs persistCredentials: true.
#
# Hang-proofing, each clause field-earned:
#   - no az CLI (extension add hangs on egress; pr create waits on
#     stdin; telemetry hangs AFTER success on restricted agents)
#   - stdin closed + GIT_TERMINAL_PROMPT=0: any credential prompt fails
#     loud instead of waiting forever
#   - curl --max-time 60 and the HTTP status checked explicitly
#   - numbered checkpoints: the last one printed names the failing step
set -euo pipefail
exec </dev/null
export GIT_TERMINAL_PROMPT=0

say() { echo "[commit-back $1] $2"; }

say 0/5 "preflight"
for v in TENANT BRANCH_PREFIX PR_TITLE SYSTEM_ACCESSTOKEN \
         SYSTEM_COLLECTIONURI SYSTEM_TEAMPROJECT BUILD_REPOSITORY_ID; do
  if [ -z "${!v:-}" ]; then
    if [ "$v" = SYSTEM_ACCESSTOKEN ]; then
      echo "error: SYSTEM_ACCESSTOKEN is unset — ADO never auto-exposes it;" \
           'map it on the step: env: { SYSTEM_ACCESSTOKEN: $(System.AccessToken) }'
    else
      echo "error: required env var $v is unset"
    fi
    exit 2
  fi
done
for v in TENANT BRANCH_PREFIX; do
  case "${!v}" in
    (*[!A-Za-z0-9_.-]*) echo "error: $v must match [A-Za-z0-9_.-]+ (got '${!v}')"; exit 2;;
  esac
done
COMMIT_PATHS="${COMMIT_PATHS:-config/$TENANT imports/$TENANT}"
TARGET_BRANCH="${TARGET_BRANCH:-main}"

say 1/5 "change detection in: $COMMIT_PATHS"
# shellcheck disable=SC2086  # COMMIT_PATHS is a deliberate word list
if [ -z "$(git status --porcelain -- $COMMIT_PATHS)" ]; then
  say 1/5 "nothing to commit — clean exit"
  exit 0
fi

branch="$BRANCH_PREFIX/$TENANT/$(date -u +%Y%m%dT%H%M%S)"
say 2/5 "branch + commit: $branch"
git checkout -b "$branch"
for p in $COMMIT_PATHS; do
  if [ -e "$p" ]; then git add -- "$p"; fi
done
git -c user.name="$BRANCH_PREFIX-bot" -c user.email="$BRANCH_PREFIX-bot@invalid" \
  commit -m "$PR_TITLE"

say 3/5 "push (needs Contribute + persistCredentials)"
git push -u origin "$branch"

say 4/5 "PR body"
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT
desc="Automated commit-back from $branch."
if [ -n "${DESCRIPTION_FILE:-}" ]; then
  desc="$(head -c 3800 "$DESCRIPTION_FILE" 2>/dev/null || echo "Commit-back (report missing from workspace)")"
fi
if [ -n "${ARTIFACT_NOTE:-}" ]; then
  desc="$desc

$ARTIFACT_NOTE"
fi
python3 - "$branch" "$TARGET_BRANCH" "$PR_TITLE" "$desc" > "$tmpdir/pr.json" <<'PYEOF'
import json, sys
print(json.dumps({
    "sourceRefName": "refs/heads/" + sys.argv[1],
    "targetRefName": "refs/heads/" + sys.argv[2],
    "title": sys.argv[3],
    "description": sys.argv[4],
}))
PYEOF

say 5/5 "open PR via REST"
# Proxy visibility WITHOUT echoing the value (proxy URLs can carry
# credentials). On proxied networks the proxy must be declared on THIS
# step's env: git push can ride agent-level git config, but curl reads
# only the environment — field-hit: branch pushed, REST call hung.
if [ -n "${HTTPS_PROXY:-${https_proxy:-}}" ]; then
  say 5/5 "https proxy: set"
else
  say 5/5 "https proxy: unset (direct egress)"
fi
# Project names may carry spaces — encode the URL path segment.
project="$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1]))' "$SYSTEM_TEAMPROJECT")"
url="${SYSTEM_COLLECTIONURI}${project}/_apis/git/repositories/${BUILD_REPOSITORY_ID}/pullrequests?api-version=7.0"
code="$(curl -sS --max-time 60 -X POST \
  -H "Authorization: Bearer $SYSTEM_ACCESSTOKEN" \
  -H "Content-Type: application/json" \
  -d @"$tmpdir/pr.json" -o "$tmpdir/resp.json" -w '%{http_code}' "$url")" || {
    echo "error: curl transport failure (egress/timeout/proxy) — branch $branch IS pushed; if this network rides a proxy, declare HTTPS_PROXY on this step's env; open the PR by hand or re-run"
    exit 1
  }
case "$code" in
  2*) say 5/5 "PR opened from $branch (HTTP $code)";;
  *)  echo "error: PR creation failed (HTTP $code) — branch $branch IS pushed:"
      cat "$tmpdir/resp.json"
      exit 1;;
esac
