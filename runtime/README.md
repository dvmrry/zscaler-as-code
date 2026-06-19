# runtime/ -- the modern (3.10+) layer

`runtime/` is the uv-managed, Python 3.10+ side of zscaler-as-code. It runs ONLY
under uv (`requires-python >= 3.10`) and is kept strictly separate from `tools/`,
which stays on the Python 3.6 floor (bare stdlib, `%`-formatting, no f-strings).

## The one-way import rule

- `runtime/` MAY import from `tools/`.
- `tools/` MUST NEVER import from `runtime/`.

This keeps the 3.6-floor tooling (which has to run under `python:3.6.8-slim` in
CI and in restricted environments) free of any 3.10+ dependency. The rule is
enforced by `make check-imports`, which greps `tools/` for any import of
`zac_runtime` and fails if one is found.

In v1 the only real code is the gate runner (`zac_runtime.gate`), which shells
`make` and imports nothing from `tools/`, so the wall is trivially satisfied.
Future `runtime/` code MAY import `tools/`.

## What is here

- `src/zac_runtime/gate.py` -- the gate runner. Runs a `make` target, classifies
  the result (`pass` on exit 0, `blocked` otherwise), and writes a deterministic
  status artifact to `outputs/gates/<gate>.status.json`.
- `src/zac_runtime/__main__.py` -- `python -m zac_runtime` passthrough.
- `tests/test_gate.py` -- unit tests (subprocess.run monkeypatched).

## Running

```bash
# via the installed console script (the `make gate` target uses this form):
uv run --project runtime zac-gate typecheck TENANT=demo

# tests:
uv run --project runtime python -m unittest discover -s runtime/tests -t runtime -v
```

Artifacts under `outputs/` are gitignored: a blocked-case `tail` can echo
config-derived strings, so they are pipeline scratch and never committed.
