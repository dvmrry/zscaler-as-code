#!/usr/bin/env bash
# Commit-back: push generated config/imports to STABLE per-(tenant,
# resource-type) branches and open ONE Azure DevOps PR per changed
# resource type via REST. Called by the bootstrap/drift pipelines - the
# YAML stays a thin caller so this logic updates on repo pull (adapted
# pipeline YAML does not; field-hit repeatedly).
#
# One rolling PR per changed resource type:
#   <BRANCH_PREFIX>/<TENANT>/<resource_type>
# The branch is STABLE (no timestamp) and force-pushed each run, so a type
# that re-drifts REFRESHES its open PR instead of stacking a new one; a new
# PR appears only after the previous is merged/closed. drift and bootstrap
# stay separate by BRANCH_PREFIX, so their lifecycles never collide.
#
# Required env:
#   TENANT              tenant label ([A-Za-z0-9_.-]+)
#   BRANCH_PREFIX       branch namespace: drift | bootstrap ([A-Za-z0-9_.-]+)
#   PR_TITLE            base PR title; " - <resource_type>" is appended per PR
#   SYSTEM_ACCESSTOKEN  map EXPLICITLY in the YAML step:
#                         env: { SYSTEM_ACCESSTOKEN: $(System.AccessToken) }
#                       (ADO auto-exposes the other SYSTEM_*/BUILD_* vars; not
#                       this one)
# Optional env:
#   TARGET_BRANCH       PR target (default: main)
#   PR_BODY_FILE        markdown file to use as every PR body (for drift,
#                       reports/<tenant>/drift.md)
#   ARTIFACT_NOTE       extra line appended to every PR body (e.g. a pointer
#                       to the published full drift report)
#   HTTPS_PROXY         declare on the step if egress rides a proxy: curl
#                       reads ONLY the env - agent git config gets the push
#                       through and then the REST call hangs (field-hit)
#
# ADO setup: the build service identity needs "Contribute" on the repo;
# checkout needs persistCredentials: true.
#
# Hang-proofing, each clause field-earned: no az CLI (extension add hangs on
# egress; pr create waits on stdin; telemetry hangs AFTER success); stdin
# closed + GIT_TERMINAL_PROMPT=0 (credential prompts fail loud); curl
# --max-time 60 with the HTTP status checked; numbered checkpoints (the last
# one printed names the failing step).
set -euo pipefail
exec </dev/null
export GIT_TERMINAL_PROMPT=0

say() { echo "[commit-back $1] $2"; }

# --- preflight ----------------------------------------------------------
say 0/5 "preflight"
for v in TENANT BRANCH_PREFIX PR_TITLE SYSTEM_ACCESSTOKEN \
         SYSTEM_COLLECTIONURI SYSTEM_TEAMPROJECT BUILD_REPOSITORY_ID; do
  if [ -z "${!v:-}" ]; then
    if [ "$v" = SYSTEM_ACCESSTOKEN ]; then
      echo "error: SYSTEM_ACCESSTOKEN is unset - ADO never auto-exposes it;" \
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
TARGET_BRANCH="${TARGET_BRANCH:-main}"
CONFIG_DIR="config/$TENANT"
IMPORTS_DIR="imports/$TENANT"

# --- which resource types changed? --------------------------------------
say 1/5 "change detection in $CONFIG_DIR + $IMPORTS_DIR"
# NB: the program comes from `-c`, not a heredoc - stdin is the piped
# `git status` output, and a `<<EOF` heredoc would override that pipe
# (git would then SIGPIPE into a dead reader).
types="$(git status --porcelain --untracked-files=all -- "$CONFIG_DIR" "$IMPORTS_DIR" | python3 -c '
import re, sys
t = re.escape(sys.argv[1])
cfg = re.compile(r"^config/%s/(.+?)(?:\.auto\.tfvars|\.lookup)\.json$" % t)
imp = re.compile(r"^imports/%s/(.+?)_(?:imports|moves)\.tf$" % t)
out = set()
for line in sys.stdin:
    line = line.rstrip("\n")
    if len(line) < 4:
        continue
    path = line[3:]                       # porcelain: 2 status chars + space
    if " -> " in path:                    # rename: take the new path
        path = path.split(" -> ")[-1]
    m = cfg.match(path) or imp.match(path)
    if m:
        out.add(m.group(1))
for name in sorted(out):
    print(name)
' "$TENANT")"
if [ -z "$types" ]; then
  say 1/5 "nothing to commit - clean exit"
  exit 0
fi
say 1/5 "changed types:$(printf ' %s' $types)"
if [ -n "${PR_BODY_FILE:-}" ] && ! [ -s "$PR_BODY_FILE" ]; then
  echo "error: PR_BODY_FILE is set but missing or empty: $PR_BODY_FILE"
  exit 2
fi

# --- snapshot the working tree so per-type branches can cherry-pick paths ---
# A throwaway commit captures every change (incl. NEW files) in one tree we
# can restore individual paths from; the working tree is then clean, so each
# `checkout -B <branch> <base>` below never hits "local changes would be
# overwritten".
say 2/5 "snapshot working tree"
start_ref="$(git rev-parse HEAD)"
tmpdir="$(mktemp -d)"
# Trap armed BEFORE the temp branch is created, so a failure during the
# snapshot still returns the repo to the start ref and removes the temp
# branch - otherwise the next run cannot recreate _commitback_snap and the
# pipeline wedges until someone intervenes.
trap 'git checkout -q "$start_ref" 2>/dev/null || true; \
      git branch -qD _commitback_snap 2>/dev/null || true; \
      rm -rf "$tmpdir"' EXIT
git branch -qD _commitback_snap 2>/dev/null || true   # leftover from a prior failed run
git checkout -q -b _commitback_snap
# Add only the dirs that exist - a config-only first bootstrap may have no
# imports/<tenant> yet, and `git add` errors (exit 128) on a pathspec that
# matches nothing.
for d in "$CONFIG_DIR" "$IMPORTS_DIR"; do
  [ -e "$d" ] && git add -A -- "$d"
done
git -c user.name="$BRANCH_PREFIX-bot" -c user.email="$BRANCH_PREFIX-bot@invalid" \
  commit -q -m "[commitback snapshot]" --no-verify
wip="$(git rev-parse HEAD)"
git checkout -q "$start_ref"            # detach back to start; snapshot held by $wip

say 3/5 "fetch $TARGET_BRANCH (PR base)"
git fetch -q origin "$TARGET_BRANCH"
base="$(git rev-parse FETCH_HEAD)"
# proxy visibility WITHOUT echoing the value (proxy URLs can carry creds)
if [ -n "${HTTPS_PROXY:-${https_proxy:-}}" ]; then
  say 3/5 "https proxy: set"
else
  say 3/5 "https proxy: unset (direct egress)"
fi

enc_proj="$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1]))' "$SYSTEM_TEAMPROJECT")"
pr_url="${SYSTEM_COLLECTIONURI}${enc_proj}/_apis/git/repositories/${BUILD_REPOSITORY_ID}/pullrequests?api-version=7.0"

body_for_pr() {   # $1 branch
  local br="$1"
  if [ -n "${PR_BODY_FILE:-}" ]; then
    cat "$PR_BODY_FILE"
  else
    printf 'Automated %s for %s - resource type `%s`.\n' \
      "$BRANCH_PREFIX" "$TENANT" "${br##*/}"
  fi
  if [ -n "${ARTIFACT_NOTE:-}" ]; then
    printf '\n%s\n' "$ARTIFACT_NOTE"
  fi
}

open_pr() {   # $1 branch  $2 title
  local br="$1" title="$2" body_file code
  body_file="$tmpdir/pr-body-${br##*/}.md"
  body_for_pr "$br" > "$body_file"
  python3 - "$br" "$TARGET_BRANCH" "$title" "$body_file" > "$tmpdir/pr.json" <<'PYEOF'
import json, sys
REPLACEMENTS = {
    "\u00a0": " ",
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2013": "-",
    "\u2014": "--",
    "\u2026": "...",
    "\u2212": "-",
}
with open(sys.argv[4], encoding="utf-8") as f:
    desc = f.read()
for old, new in REPLACEMENTS.items():
    desc = desc.replace(old, new)
desc = desc.encode("ascii", "replace").decode("ascii")
print(json.dumps({
    "sourceRefName": "refs/heads/" + sys.argv[1],
    "targetRefName": "refs/heads/" + sys.argv[2],
    "title": sys.argv[3],
    "description": desc,
}))
PYEOF
  code="$(curl -sS --max-time 60 -X POST \
    -H "Authorization: Bearer $SYSTEM_ACCESSTOKEN" -H "Content-Type: application/json" \
    -d @"$tmpdir/pr.json" -o "$tmpdir/resp.json" -w '%{http_code}' "$pr_url")" || {
      echo "  error: curl transport failure for $br (egress/timeout/proxy) - branch IS pushed; if proxied, declare HTTPS_PROXY; open the PR by hand"
      return 1; }
  case "$code" in
    2*)  echo "  PR opened for $br (HTTP $code)";;
    409) echo "  $br: active PR already open - refreshed by the push (HTTP 409)";;
    *)   echo "  error: PR create for $br failed (HTTP $code):"; cat "$tmpdir/resp.json"; return 1;;
  esac
}

# NB: process_type is called as `if process_type ...`, which disables
# `set -e` inside it - so every load-bearing git step checks its own exit
# and returns 1 explicitly. A bare failing command here would otherwise
# fall through (e.g. a failed push followed by a 409 reporting success).
process_type() {   # $1 resource type
  local t="$1" br="$BRANCH_PREFIX/$TENANT/$1" f
  git checkout -q -B "$br" "$base" || { echo "  $t: checkout failed"; return 1; }
  # Bring this type's changes from the snapshot, handling add/modify AND
  # delete: present in the snapshot -> restore it; gone from the snapshot
  # but present in the base -> stage the removal (a resource dropped
  # upstream), so deletions propagate instead of being silently skipped.
  for f in "$CONFIG_DIR/$t.auto.tfvars.json" \
           "$CONFIG_DIR/$t.lookup.json" \
           "$IMPORTS_DIR/${t}_imports.tf" "$IMPORTS_DIR/${t}_moves.tf"; do
    if git cat-file -e "$wip:$f" 2>/dev/null; then
      git checkout -q "$wip" -- "$f"
    elif git cat-file -e "$base:$f" 2>/dev/null; then
      git rm -q --cached -- "$f" >/dev/null 2>&1 || true
      rm -f "$f"
    fi
  done
  if git diff --cached --quiet; then
    echo "  $t: no net change vs $TARGET_BRANCH - skip"
    return 0
  fi
  git -c user.name="$BRANCH_PREFIX-bot" -c user.email="$BRANCH_PREFIX-bot@invalid" \
    commit -q -m "$PR_TITLE - $t" || { echo "  $t: commit failed"; return 1; }
  git fetch -q origin "$br" 2>/dev/null || true   # give --force-with-lease a basis if the branch exists
  git push -q --force-with-lease origin "$br" \
    || { echo "  $t: push FAILED - branch not updated"; return 1; }
  open_pr "$br" "$PR_TITLE - $t"
}

say 4/5 "per-type branches + PRs"
failed=0
for t in $types; do
  if process_type "$t"; then :; else failed=$((failed + 1)); fi
done

if [ "$failed" -ne 0 ]; then
  say 5/5 "$failed resource type(s) FAILED - see errors above; the rest succeeded"
  exit 1
fi
say 5/5 "done - one rolling PR per changed resource type"
