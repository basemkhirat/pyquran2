"""Reshape Arabic for correct terminal display (joined glyphs, RTL) using arabic-reshaper + python-bidi."""

import arabic_reshaper
from bidi.algorithm import get_display


def display_arabic(s: str) -> str:
    """Return string suitable for terminal: Arabic reshaped and bidirectional reordered."""
    if not s or not _has_arabic(s):
        return s
    reshaped = arabic_reshaper.reshape(s)
    return get_display(reshaped)


def _has_arabic(s: str) -> bool:
    """True if string contains any character in the Arabic Unicode block."""
    for c in s:
        if "\u0600" <= c <= "\u06FF":
            return True
    return False
