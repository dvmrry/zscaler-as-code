"""Every text-mode open() in tools/ must pin encoding="utf-8".

open()'s default encoding is the LOCALE's (locale.getpreferredencoding) —
generated files carry UTF-8 em-dashes in their headers, so an agent
running under a latin-1/C locale reads goldens as mojibake (GoldenTest
false-failures) or dies writing the generated text. Field-hit 2026-06-11.
Binary-mode opens are exempt; everything else pins utf-8 explicitly
(the kwarg exists since Python 3.0 — floor-safe).

The scanner must itself run on BOTH interpreters this suite targets, and
string-literal AST nodes differ across them: 3.6/3.7 parse them as
ast.Str (removed in 3.14), 3.8+ as ast.Constant (never emitted by the
3.6/3.7 parser — a Constant-only check would be silently vacuous on the
floor). Version-gated below; ScannerSelfTest pins non-vacuousness on
whichever interpreter is running.
"""
import ast
import os
import sys
import unittest

_LEGACY_PARSER = sys.version_info < (3, 8)


def _string_literal(node):
    """The str value of a string-literal node, else None — both parsers."""
    if _LEGACY_PARSER:
        if isinstance(node, ast.Str):
            return node.s
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _violations_in_source(source, label):
    tree = ast.parse(source)
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "open"):
            continue  # method .open() calls (urllib openers) are not file opens
        kwargs = {k.arg for k in node.keywords if k.arg}
        if "encoding" in kwargs:
            continue
        mode = None
        if len(node.args) >= 2:
            mode = _string_literal(node.args[1])
        for k in node.keywords:
            if k.arg == "mode":
                literal = _string_literal(k.value)
                if literal is not None:
                    mode = literal
        if mode is not None and "b" in mode:
            continue
        bad.append("%s:%d" % (label, node.lineno))
    return bad


def _violations(path):
    with open(path, "rb") as f:
        return _violations_in_source(f.read(), path)


class ScannerSelfTest(unittest.TestCase):
    """The scanner must detect violations on THIS interpreter — a parser
    mismatch (ast.Str vs ast.Constant) degrades silently into mode
    misdetection, so non-vacuousness is pinned here, not assumed."""

    def test_detects_missing_encoding(self):
        src = 'with open("x.txt") as f:\n    f.read()\n'
        self.assertEqual(len(_violations_in_source(src, "t")), 1)

    def test_binary_mode_exempt_requires_literal_detection(self):
        # Exemption only works if the mode STRING LITERAL is recognized by
        # this interpreter's parser — the cross-version seam under test.
        src = 'with open("x.bin", "rb") as f:\n    f.read()\n'
        self.assertEqual(_violations_in_source(src, "t"), [])
        src_kw = 'with open("x.bin", mode="wb") as f:\n    pass\n'
        self.assertEqual(_violations_in_source(src_kw, "t"), [])

    def test_text_write_mode_flagged(self):
        src = 'with open("x.txt", "w") as f:\n    pass\n'
        self.assertEqual(len(_violations_in_source(src, "t")), 1)

    def test_encoding_kwarg_passes(self):
        src = 'with open("x.txt", "w", encoding="utf-8") as f:\n    pass\n'
        self.assertEqual(_violations_in_source(src, "t"), [])


class EncodingDisciplineTest(unittest.TestCase):
    def test_all_text_opens_pin_utf8(self):
        # Recursive walk of tools/ — a flat listdir of (tools, tools/tests)
        # silently skips any SUBPACKAGE (e.g. a future tools/<tool>/ dir), so
        # an unscanned subdir's open() regressions would pass `make test`
        # invisibly. dirs.sort() keeps the failure list deterministic.
        bad = []
        for root, dirs, files in os.walk("tools"):
            dirs.sort()
            for fn in sorted(files):
                if fn.endswith(".py"):
                    bad.extend(_violations(os.path.join(root, fn)))
        self.assertEqual(
            bad, [],
            "text-mode open() without encoding='utf-8' (locale-dependent; "
            "breaks on non-UTF-8 agents): %s" % ", ".join(bad),
        )


if __name__ == "__main__":
    unittest.main()
