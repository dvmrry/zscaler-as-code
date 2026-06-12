"""Package init: force UTF-8 stdio on broken-locale interpreters.

Python 3.6 lacks PEP 538/540, so on agents with a POSIX/C locale
sys.stdout/stderr come up latin-1 — and the first report line carrying
an em-dash (or any tenant data with non-ASCII) dies with
UnicodeEncodeError (field-hit: make lint in the pipeline). The
encoding="utf-8" discipline on open() calls cannot cover the standard
streams, so every `python -m tools.X` entry point gets them fixed here,
at import, conditionally: a stream that is already UTF-8 (every 3.7+
interpreter, every healthy locale) is left completely untouched.

errors="replace" because a report that prints mojibake beats a gate
that crashes mid-run. See AGENTS.md rule 5.
"""
import sys


def _ensure_utf8_stdio():
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        encoding = (getattr(stream, "encoding", None) or "").replace("-", "").lower()
        if encoding in ("utf8", "utf_8") or not hasattr(stream, "buffer"):
            continue
        import io

        setattr(sys, name, io.TextIOWrapper(
            stream.buffer, encoding="utf-8", errors="replace",
            line_buffering=True))


_ensure_utf8_stdio()
