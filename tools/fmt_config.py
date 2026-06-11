"""Rewrite a tenant's config files in canonical transform form.

The remediation for lint's canonical-form error: hand edits (CRLF, BOM,
unsorted keys, indentation drift) are normalized to exactly what
make transform would emit, so diffs stay minimal and reviews stay
readable. Content is untouched — this is formatting only.

Usage: python -m tools.fmt_config <tenant>   (make fmt-config)

Stdlib-only, Python 3.6-floor — see AGENTS.md rule 5.
"""
import json
import os
import sys

from tools.registry import generated_types
from tools.transform import render_tfvars


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        sys.stderr.write("usage: python -m tools.fmt_config <tenant>\n")
        return 2
    tenant = argv[0]
    config_dir = os.path.join("config", tenant)

    rewritten = 0
    checked = 0
    for rt in generated_types():
        path = os.path.join(config_dir, rt + ".auto.tfvars.json")
        if not os.path.exists(path):
            continue
        checked += 1
        with open(path, "rb") as f:
            raw_bytes = f.read()
        # Decode as PLAIN utf-8 (not utf-8-sig) for the comparison: a
        # leading BOM stays in `original`, so a BOM-only file compares
        # UNEQUAL to canonical and gets rewritten BOM-free below (lint
        # flags it; this is the fix). Parse the items from a BOM-stripped
        # copy, since json.loads rejects a leading BOM character.
        original = raw_bytes.decode("utf-8")
        items = json.loads(original.lstrip(u"﻿")).get("items") or {}
        canonical = render_tfvars(items)
        if original != canonical:
            with open(path, "w") as f:
                f.write(canonical)
            sys.stderr.write("rewrote %s\n" % path)
            rewritten += 1

    if not checked:
        sys.stdout.write(
            "error: no config files found for tenant %r in %s\n"
            % (tenant, config_dir))
        return 1
    sys.stdout.write(
        "%d file(s) canonical, %d rewritten\n" % (checked - rewritten, rewritten))
    return 0


if __name__ == "__main__":
    sys.exit(main())
