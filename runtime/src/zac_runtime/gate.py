"""Thin gate runner: shell a `make` target, classify the result, and write a
deterministic status artifact.

This is the only real code in runtime/ for v1. It does NOT import tools/ (it
shells `make`), so the one-way import wall (tools/ never imports runtime/) is
trivially satisfied. Modern 3.10+ idioms are fine here -- this runs only under
uv, never under the 3.6 floor.

Artifact contract: outputs/gates/<gate>.status.json, written as
json.dumps(record, indent=2, sort_keys=True) + "\n". returncode 0 -> "pass"
(no tail), nonzero -> "blocked" (last ~40 lines of combined output in `tail`).
Exit 0 on pass, 1 on blocked.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# Gate names become a make target AND the artifact filename stem; bound them to
# make-target identifiers so `zac-gate ../oops` cannot write outside artifact_dir.
GATE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

# Default-deny least-privilege: the gate runner only runs READ-ONLY validation
# gates. It must never be a path to a state-mutating target (apply / import /
# statefill / fetch / ...), so the workflow invariant "never apply, draft PR
# only" cannot be bypassed via `make gate GATE=apply`. Add a target here only
# if it is side-effect-free.
GATE_ALLOW = frozenset({
    "typecheck", "lint", "conformance", "check-imports",
    "validate", "validate-config", "validate-imports", "assert-clean",
    "refresh-gates", "test", "test-modules", "test-envs",
})

# Extra make args must be VAR=value assignments, never bare words: a bare word
# is an additional `make` GOAL, so `zac-gate typecheck apply` would run apply as
# a second target and bypass GATE_ALLOW. (`mine` is also kept OUT of the
# allowlist -- `make mine UPDATE_BASELINE=1` can mutate committed baselines.)
_VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# runtime/src/zac_runtime/gate.py -> parents[0]=zac_runtime, [1]=src,
# [2]=runtime, [3]=repo root. Confirmed correct (spec D3).
REPO_ROOT = Path(__file__).resolve().parents[3]

TAIL_LINES = 40


def _tail(text: str, n: int = TAIL_LINES) -> str:
    lines = text.splitlines()
    return "\n".join(lines[-n:])


def run_gate(gate: str, make_vars: list[str], artifact_dir: Path) -> int:
    """Run `make <gate> <make_vars...>` from REPO_ROOT, write a status artifact
    under artifact_dir, print `Status: <status>`, and return 0 (pass) or 1
    (blocked)."""
    if not GATE_RE.match(gate):
        raise ValueError(
            "gate must match %s (a make-target identifier), got %r"
            % (GATE_RE.pattern, gate))
    if gate not in GATE_ALLOW:
        raise ValueError(
            "%r is not an allowlisted read-only gate; the runner refuses "
            "state-mutating targets (apply / import / fetch / ...). "
            "Allowed: %s" % (gate, ", ".join(sorted(GATE_ALLOW))))
    bad_vars = [v for v in make_vars if not _VAR_RE.match(v)]
    if bad_vars:
        raise ValueError(
            "gate args must be VAR=value assignments, not make targets "
            "(got %r); a bare word runs as an extra `make` goal and would "
            "bypass the gate allowlist" % bad_vars)
    argv = ["make", gate, *make_vars]
    proc = subprocess.run(
        argv,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    status = "pass" if proc.returncode == 0 else "blocked"
    record: dict[str, object] = {
        "command": " ".join(argv),
        "gate": gate,
        "returncode": proc.returncode,
        "schema_version": 1,
        "status": status,
    }
    if status == "blocked":
        combined = (proc.stdout or "") + (proc.stderr or "")
        record["tail"] = _tail(combined)

    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / ("%s.status.json" % gate)
    artifact_path.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print("Status: %s" % status)
    return 0 if status == "pass" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="zac-gate",
        description="Run a make gate target and write a status artifact.",
    )
    parser.add_argument("gate", help="the make target to run as a gate")
    parser.add_argument(
        "make_vars",
        nargs="*",
        help="extra make variables, e.g. TENANT=demo",
    )
    parser.add_argument(
        "--artifact-dir",
        default=str(REPO_ROOT / "outputs" / "gates"),
        help="where to write <gate>.status.json (default: outputs/gates)",
    )
    args = parser.parse_args(argv)
    return run_gate(args.gate, args.make_vars, Path(args.artifact_dir))


if __name__ == "__main__":
    sys.exit(main())
