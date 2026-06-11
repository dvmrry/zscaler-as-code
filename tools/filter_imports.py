"""Filter an imports file down to addresses NOT already in tfstate.

Terraform errors on an import block whose `to` address is already
managed ("Resource already managed by Terraform") — import blocks are
not idempotent. Filtering against `terraform state list` makes
bootstrap re-runs and post-adoption delta imports mechanical: already-
managed blocks drop, new objects survive, no hand-editing.

Usage: python -m tools.filter_imports <imports.tf> <state-list-file>
Surviving blocks print to stdout; a kept/skipped summary to stderr.
(moved blocks need no such filtering: a moved block whose source is
absent from state is a documented no-op.)

Stdlib-only, Python 3.6-floor — see AGENTS.md rule 5.
"""
import re
import sys

_BLOCK_RE = re.compile(r"import \{[^}]*\}\n?", re.DOTALL)
_TO_RE = re.compile(r"to\s*=\s*(\S+)")


def filter_imports(imports_text, state_addresses):
    """(imports file text, set of managed addresses) -> (text, kept, skipped)."""
    managed = set(state_addresses)
    kept_blocks = []
    skipped = 0
    for block in _BLOCK_RE.findall(imports_text):
        m = _TO_RE.search(block)
        address = m.group(1) if m else None
        if address is not None and address in managed:
            skipped += 1
            continue
        kept_blocks.append(block.strip("\n"))
    text = "\n\n".join(kept_blocks)
    if text:
        text += "\n"
    return text, len(kept_blocks), skipped


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 2:
        sys.stderr.write(
            "usage: python -m tools.filter_imports <imports.tf> <state-list-file>\n"
        )
        return 2
    with open(argv[0], encoding="utf-8") as f:
        imports_text = f.read()
    with open(argv[1], encoding="utf-8") as f:
        state_addresses = [line.strip() for line in f if line.strip()]
    text, kept, skipped = filter_imports(imports_text, state_addresses)
    sys.stdout.write(text)
    sys.stderr.write(
        "%d import(s) kept, %d already managed (skipped)\n" % (kept, skipped)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
