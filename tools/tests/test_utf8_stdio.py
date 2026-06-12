"""Tests for the UTF-8 stdio coercion (tools/__init__).

Field-hit: Python 3.6 on a POSIX-locale agent brings stdout up as
latin-1, and make lint died with UnicodeEncodeError on its first
em-dash. The package init wraps non-UTF-8 standard streams at import;
these tests simulate the broken-locale stream directly so the fix is
pinned on every interpreter, not just the 3.6 floor.
"""
import io
import sys
import unittest

from tools import _ensure_utf8_stdio


class Utf8StdioTest(unittest.TestCase):
    def _latin1_stream(self):
        raw = io.BytesIO()
        return raw, io.TextIOWrapper(raw, encoding="latin-1")

    def test_latin1_stdout_is_wrapped_and_em_dash_survives(self):
        raw, broken = self._latin1_stream()
        old = sys.stdout
        sys.stdout = broken
        try:
            _ensure_utf8_stdio()
            # the exact line shape lint emits, em-dash included
            sys.stdout.write("ERROR rt: w: problem — fix it\n")
            sys.stdout.flush()
            data = raw.getvalue()  # before the wrapper is GC-closed
        finally:
            sys.stdout = old
        self.assertIn("—".encode("utf-8"), data)

    def test_unencodable_data_never_crashes_the_gate(self):
        raw, broken = self._latin1_stream()
        old = sys.stderr
        sys.stderr = broken
        try:
            _ensure_utf8_stdio()
            sys.stderr.write("value “smart” — ok\n")
            sys.stderr.flush()
            data = raw.getvalue()  # before the wrapper is GC-closed
        finally:
            sys.stderr = old
        self.assertTrue(data)  # wrote, did not raise

    def test_healthy_utf8_stream_left_untouched(self):
        raw = io.BytesIO()
        healthy = io.TextIOWrapper(raw, encoding="utf-8")
        old = sys.stdout
        sys.stdout = healthy
        try:
            _ensure_utf8_stdio()
            self.assertIs(sys.stdout, healthy)
        finally:
            sys.stdout = old

    def test_bufferless_stream_left_untouched(self):
        # tests routinely swap in StringIO (no .buffer) — never wrap it
        fake = io.StringIO()
        old = sys.stdout
        sys.stdout = fake
        try:
            _ensure_utf8_stdio()
            self.assertIs(sys.stdout, fake)
        finally:
            sys.stdout = old


if __name__ == "__main__":
    unittest.main()
