"""zac_runtime -- the modern (3.10+) runtime layer for zscaler-as-code.

This package runs ONLY under uv (requires-python >= 3.10). It MAY import from
tools/, but tools/ must NEVER import from here (the one-way wall; enforced by
`make check-imports`). In v1 the gate runner shells `make` and imports nothing
from tools/, so the wall is trivially satisfied.
"""
