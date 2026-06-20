"""Guard the overlay-migration runbook's load-bearing hygiene (spec Major K).

A migration step is copy-pasted into a real terminal. A literal ``$(OVERLAY)``
in a fenced shell command runs as a command substitution (executing a program
named ``OVERLAY``), silently producing the wrong path — exactly the failure the
design spec calls out. These tests pin the runbook so that regression can't slip
back in: the overlay section must exist, must carry the load-bearing precondition
(verify ``make print-overlay`` BEFORE ``gen-env``), and no fenced shell line may
contain a bare ``$(OVERLAY)``.
"""
import os
import re
import unittest

RUNBOOK = "RUNBOOK.md"
SECTION_ANCHOR = "## Migrating a Deployment to an Overlay"


def _read():
    with open(RUNBOOK, encoding="utf-8") as f:
        return f.read()


def _overlay_section(text):
    start = text.index(SECTION_ANCHOR)
    # The section runs to the next top-level (## ) heading or EOF.
    rest = text[start + len(SECTION_ANCHOR):]
    nxt = re.search(r"\n## ", rest)
    return rest[: nxt.start()] if nxt else rest


def _fenced_lines(section):
    """Yield the lines inside ``` fenced blocks (the copy-paste commands)."""
    in_fence = False
    for line in section.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            yield line


class RunbookOverlayTest(unittest.TestCase):
    def test_overlay_migration_section_exists(self):
        self.assertIn(SECTION_ANCHOR, _read())

    def test_no_fenced_command_contains_literal_overlay_var(self):
        section = _overlay_section(_read())
        offenders = [l for l in _fenced_lines(section) if "$(OVERLAY)" in l]
        self.assertEqual(
            offenders, [],
            "copy-paste command(s) contain a literal $(OVERLAY) (runs as a shell "
            "command substitution): %s" % offenders)

    def test_precondition_verifies_overlay_before_gen_env(self):
        section = _overlay_section(_read())
        self.assertIn("make print-overlay", section)
        # The precondition must land BEFORE the first gen-env in the section.
        self.assertLess(
            section.index("make print-overlay"),
            section.index("make gen-env"),
            "the print-overlay precondition must precede gen-env in the runbook")


if __name__ == "__main__":
    unittest.main()
