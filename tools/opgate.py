"""Deterministic gate-artifact helper for the operate-change workflow.

The harness (docs/workflows/operate-change.harness.md) drives a weak agent through
a fixed phase chain; this helper is the deterministic floor under each gate: it
creates a status artifact, verifies the required fields, reads it back, and exits
0 on `pass` / nonzero on `blocked`. The agent answers "where am I / what next?"
from the artifact, never from memory.

Subcommands:
  intake   -- structured capture of the ticket (no gate; status always pass)
  resolve  -- GATE G1: each target must resolve to exactly one config key
  validate -- GATE G2: `make typecheck` then `make lint` must both exit 0
  status   -- read-only: print phase/status/next_step of the latest artifact

Artifacts live under outputs/operate/<slug>/ and are written canonically:
json.dumps(record, indent=2, sort_keys=True) + "\n" (sorted keys, trailing
newline, ASCII). `status` enum is `pass` | `blocked`; a hard tooling error is
surfaced as `blocked` with the cause in `blocking_issues`. `phase` enum is
`intake | resolve | validate`.

Stdlib-only, Python 3.6-floor (bare stdlib, %-formatting, no f-strings). Imports
ONLY tools.operate (a tools->tools import) and stdlib; NEVER runtime/.
"""
import argparse
import json
import os
import re
import subprocess
import sys

from tools.operate import _check_tenant, resolve

SCHEMA_VERSION = 1
OUTPUT_ROOT = os.path.join("outputs", "operate")
SLUG_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}-[A-Za-z0-9_-]+$")

# Phase -> the artifact filename it writes (numbered so `status` can pick the
# latest by lexical sort). Apply (02) and PR (04) are prose phases handled by the
# harness, not by this helper.
_PHASE_FILE = {
    "intake": "00-intake.json",
    "resolve": "01-resolve.json",
    "validate": "03-validate.json",
}

TAIL_LINES = 40


def _check_slug(slug):
    if not SLUG_RE.match(slug or ""):
        raise ValueError(
            "slug must match %s (e.g. 2026-06-18-block-reddit), got %r"
            % (SLUG_RE.pattern, slug))


def _slug_dir(slug):
    """outputs/operate/<slug>/ -- built ONLY after the slug regex passes."""
    _check_slug(slug)
    return os.path.join(OUTPUT_ROOT, slug)


def _tail(text, n=TAIL_LINES):
    lines = text.splitlines()
    return "\n".join(lines[-n:])


def _write_artifact(slug, filename, record):
    d = _slug_dir(slug)
    if not os.path.isdir(d):
        os.makedirs(d)
    path = os.path.join(d, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return path


def _base_record(slug, tenant, phase, status, next_step, blocking_issues):
    return {
        "blocking_issues": list(blocking_issues),
        "next_step": next_step,
        "phase": phase,
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "tenant": tenant,
        "ticket_slug": slug,
    }


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def cmd_intake(slug, tenant, intake_json):
    """Structured capture -- never gated. Validate slug + tenant, copy the parsed
    targets into 00-intake.json. status always `pass`."""
    _check_slug(slug)
    _check_tenant(tenant)
    parsed = _load_json(intake_json)
    targets = parsed.get("targets") if isinstance(parsed, dict) else None
    record = _base_record(
        slug, tenant, "intake", "pass",
        "make op-resolve SLUG=%s TENANT=%s TARGETS=<path>" % (slug, tenant),
        [])
    record["targets"] = targets if isinstance(targets, list) else []
    path = _write_artifact(slug, _PHASE_FILE["intake"], record)
    # read back -> report
    back = _load_json(path)
    sys.stdout.write("intake: %s (%d target(s))\n"
                     % (back["status"], len(back["targets"])))
    return 0


def cmd_resolve(slug, tenant, target_json):
    """GATE G1: each target must resolve to EXACTLY one config key. Zero or many
    -> blocked with candidate lines. Writes 01-resolve.json."""
    _check_slug(slug)
    _check_tenant(tenant)
    parsed = _load_json(target_json)
    raw_targets = parsed.get("targets") if isinstance(parsed, dict) else None

    out_targets = []
    blocking = []
    if not isinstance(raw_targets, list):
        blocking.append(
            "target file has no 'targets' list (got %s); G1 needs a non-empty "
            "list of targets" % type(raw_targets).__name__)
        raw_targets = []
    elif not raw_targets:
        blocking.append(
            "no targets to resolve; G1 must resolve at least one target "
            "before Apply")
    for t in raw_targets:
        if not isinstance(t, dict):
            blocking.append("malformed target (not an object): %r" % (t,))
            continue
        area = t.get("area")
        field = t.get("field")
        display = t.get("display_name")
        op = t.get("op")
        values = t.get("values")
        if values is None:
            values = []
        if not isinstance(values, list):
            blocking.append(
                "%s / %r: values must be a list, got %r"
                % (area, display, values))
            continue
        if not (isinstance(field, str) and field.strip()
                and isinstance(op, str) and op.strip()):
            blocking.append(
                "%s / %r: target needs a non-empty 'field' and 'op' (got "
                "field=%r, op=%r); a resolvable name is not a complete edit"
                % (area, display, field, op))
            continue
        entry = {
            "area": area,
            "config_key": None,
            "display_name": display,
            "field": field,
            "op": op,
            "values": values,
        }
        try:
            hits = resolve(tenant, area, display)
        except ValueError as exc:
            blocking.append("%s / %r: %s" % (area, display, exc))
            out_targets.append(entry)
            continue
        if len(hits) == 1:
            entry["config_key"] = hits[0][0]
        elif len(hits) == 0:
            blocking.append("%s / %r: no match" % (area, display))
        else:
            cand = ", ".join("%s (%s)" % (k, n) for k, n in hits)
            blocking.append(
                "%s / %r: %d matches -- %s" % (area, display, len(hits), cand))
        out_targets.append(entry)

    status = "pass" if not blocking else "blocked"
    if status == "pass":
        next_step = (
            "resolve passed -- P3 Apply (prose loop): run one allowlisted "
            "make primitive per resolved target (url-add / rule-disable / "
            "...), record each one-line result into 02-apply.json, THEN "
            "make op-validate SLUG=%s TENANT=%s" % (slug, tenant))
    else:
        next_step = ("resolve blocked -- clarify the display name(s), then "
                     "re-run make op-resolve SLUG=%s TENANT=%s TARGETS=<path>"
                     % (slug, tenant))
    record = _base_record(slug, tenant, "resolve", status, next_step, blocking)
    record["targets"] = out_targets
    path = _write_artifact(slug, _PHASE_FILE["resolve"], record)
    back = _load_json(path)
    sys.stdout.write("resolve: %s (%d target(s), %d blocking)\n"
                     % (back["status"], len(back["targets"]),
                        len(back["blocking_issues"])))
    return 0 if status == "pass" else 1


def cmd_validate(slug, tenant, runner=None):
    """GATE G2: `make typecheck` then `make lint` (both with TENANT=<t>). Both
    exit 0 -> pass, else blocked with each failing command's tail. Writes
    03-validate.json. `runner` defaults to subprocess.run (injectable for tests).
    """
    _check_slug(slug)
    _check_tenant(tenant)
    if runner is None:
        def runner(argv):
            # 3.6 floor: capture_output/text= are 3.7+; use PIPE +
            # universal_newlines for the same captured-text behavior.
            return subprocess.run(
                argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True)

    checks = []
    blocking = []
    for name in ("typecheck", "lint"):
        proc = runner(["make", name, "TENANT=" + tenant])
        rc = proc.returncode
        checks.append({"name": name, "exit": rc})
        if rc != 0:
            out = getattr(proc, "stdout", "") or ""
            err = getattr(proc, "stderr", "") or ""
            tail = _tail(out + err)
            blocking.append("make %s exited %d: %s" % (name, rc, tail))

    status = "pass" if not blocking else "blocked"
    if status == "pass":
        next_step = ("validate passed -- proceed to PR (G3): commitback "
                     "checkpoint + DRAFT PR; never terraform apply")
    else:
        next_step = ("validate blocked -- fix the reported typecheck/lint "
                     "findings, then re-run make op-validate SLUG=%s TENANT=%s"
                     % (slug, tenant))
    record = _base_record(slug, tenant, "validate", status, next_step, blocking)
    record["checks"] = checks
    path = _write_artifact(slug, _PHASE_FILE["validate"], record)
    back = _load_json(path)
    sys.stdout.write("validate: %s (%s)\n"
                     % (back["status"],
                        ", ".join("%s=%d" % (c["name"], c["exit"])
                                  for c in back["checks"])))
    return 0 if status == "pass" else 1


def cmd_status(slug):
    """Read-only: print phase/status/next_step of the highest-numbered STATUS
    artifact -- skips prose Apply/PR and corrupt files so resume never reads a
    fabricated None status as a pass."""
    d = _slug_dir(slug)
    if not os.path.isdir(d):
        sys.stderr.write("no artifacts for slug %s (run make op-intake first)\n"
                         % slug)
        return 1
    files = sorted(f for f in os.listdir(d) if f.endswith(".json"))
    if not files:
        sys.stderr.write("no artifacts for slug %s\n" % slug)
        return 1
    # The helper writes status-schema artifacts (00/01/03); Apply and PR (02/04)
    # are prose the agent hand-writes. Report the latest artifact that actually
    # carries the status schema, never fabricating a None status from a prose or
    # corrupt file -- that would print on resume as a false "all clear".
    record = None
    latest_status = None
    for name in reversed(files):
        try:
            candidate = _load_json(os.path.join(d, name))
        except ValueError:
            continue
        if isinstance(candidate, dict) and all(
                k in candidate for k in ("phase", "status", "next_step")):
            record, latest_status = candidate, name
            break
    if record is None:
        sys.stderr.write(
            "no status artifact for slug %s (latest file %s carries no "
            "phase/status/next_step -- a prose Apply/PR or corrupt artifact); "
            "re-run the last gate\n" % (slug, files[-1]))
        return 1
    extra = ""
    if files[-1] != latest_status:
        extra = ("\nin_progress: %s (prose phase after the last gate)"
                 % files[-1])
    sys.stdout.write(
        "phase: %s\nstatus: %s\nnext_step: %s%s\n"
        % (record.get("phase"), record.get("status"),
           record.get("next_step"), extra))
    return 0


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    parser = argparse.ArgumentParser(
        prog="tools.opgate",
        description="Gate-artifact helper for the operate-change workflow.")
    sub = parser.add_subparsers(dest="cmd")

    p_intake = sub.add_parser("intake")
    p_intake.add_argument("--slug", required=True)
    p_intake.add_argument("--tenant", required=True)
    p_intake.add_argument("--intake-json", required=True)

    p_resolve = sub.add_parser("resolve")
    p_resolve.add_argument("--slug", required=True)
    p_resolve.add_argument("--tenant", required=True)
    p_resolve.add_argument("--target-json", required=True)

    p_validate = sub.add_parser("validate")
    p_validate.add_argument("--slug", required=True)
    p_validate.add_argument("--tenant", required=True)

    p_status = sub.add_parser("status")
    p_status.add_argument("--slug", required=True)

    args = parser.parse_args(argv)
    if args.cmd is None:
        parser.print_usage(sys.stderr)
        return 2
    try:
        if args.cmd == "intake":
            return cmd_intake(args.slug, args.tenant, args.intake_json)
        if args.cmd == "resolve":
            return cmd_resolve(args.slug, args.tenant, args.target_json)
        if args.cmd == "validate":
            return cmd_validate(args.slug, args.tenant)
        if args.cmd == "status":
            return cmd_status(args.slug)
    except ValueError as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
