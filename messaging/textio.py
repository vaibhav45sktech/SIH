"""
Terminal output encoding.

On Windows, sys.stdout defaults to the legacy ANSI code page (cp1252), which
cannot encode Devanagari or Gurmukhi. Printing an advisory therefore raises
UnicodeEncodeError and takes the whole CLI down.

For a system whose entire purpose is delivering non-Latin text, the operator
tooling must never die trying to display it. Every CLI entrypoint calls
ensure_utf8_output() before printing anything.

This affects display only. Messages stored in SQLite and handed to providers
are always Python str and were never at risk.
"""

from __future__ import annotations

import sys


def ensure_utf8_output() -> bool:
    """
    Reconfigure stdout/stderr to UTF-8 where possible.

    Returns True if both streams can handle non-Latin text afterwards.
    Safe to call repeatedly, and never raises - a console that cannot be
    reconfigured falls back to replacement characters rather than crashing.
    """
    ok = True
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue

        encoding = (getattr(stream, "encoding", "") or "").lower()
        if encoding.replace("-", "") in ("utf8", "utf8mb4"):
            continue

        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            # Not a TextIOWrapper (e.g. pytest's capture object). Leave it.
            ok = False
            continue

        try:
            # errors="replace" so an unmappable glyph degrades to a placeholder
            # instead of aborting a dispatch run mid-report.
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, AttributeError, OSError):
            ok = False

    return ok


def supports_unicode() -> bool:
    """Whether stdout can currently encode Gurmukhi (used for box drawing)."""
    encoding = (getattr(sys.stdout, "encoding", "") or "").lower()
    if not encoding:
        return False
    try:
        "ਪਾਣੀ─┌".encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True
