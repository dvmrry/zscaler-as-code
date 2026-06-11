"""Every text-mode open() in tools/ must pin encoding="utf-8".

open()'s default encoding is the LOCALE's (locale.getpreferredencoding) —
generated files carry UTF-8 em-dashes in their headers, so an agent
running under a latin-1/C locale reads goldens as mojibake (GoldenTest
false-failures) or dies writing the generated text. Field-hit 2026-06-11.
Binary-mode opens are exempt; everything else pins utf-8 explicitly
(the kwarg exists since Python 3.0 — floor-safe).
"""
import ast
import os
import unittest


def _violations(path):
    with open(path, "rb") as f:
        tree = ast.parse(f.read())
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
        if len(node.args) >= 2 and isinstance(node.args[1], ast.Str):
            mode = node.args[1].s
        for k in node.keywords:
            if k.arg == "mode" and isinstance(k.value, ast.Str):
                mode = k.value.s
        if mode is not None and "b" in mode:
            continue
        bad.append("%s:%d" % (path, node.lineno))
    return bad


class EncodingDisciplineTest(unittest.TestCase):
    def test_all_text_opens_pin_utf8(self):
        bad = []
        for d in ("tools", os.path.join("tools", "tests")):
            for fn in sorted(os.listdir(d)):
                if fn.endswith(".py"):
                    bad.extend(_violations(os.path.join(d, fn)))
        self.assertEqual(
            bad, [],
            "text-mode open() without encoding='utf-8' (locale-dependent; "
            "breaks on non-UTF-8 agents): %s" % ", ".join(bad),
        )


if __name__ == "__main__":
    unittest.main()
