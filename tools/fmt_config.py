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
            original = f.read().decode("utf-8-sig")
        canonical = render_tfvars(json.loads(original).get("items") or {})
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
