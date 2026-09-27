"""Cheap document-language guess, used to ask the AI for descriptions in the
same language as the document instead of always Bahasa Indonesia."""
from __future__ import annotations

import re

DEFAULT_LANGUAGE = "Bahasa Indonesia"

_EN = {"the", "and", "of", "to", "in", "is", "for", "with", "on", "that", "are",
       "by", "be", "this", "it", "as", "from", "or", "an", "at", "we", "your", "our"}
_ID = {"yang", "dan", "di", "ke", "dari", "untuk", "dengan", "ini", "itu", "adalah",
       "pada", "dalam", "akan", "tidak", "atau", "kami", "oleh", "sebagai", "juga"}
_WORD = re.compile(r"[a-z]+")


def detect_language(text: str) -> str:
    sample = text[:50_000]
    letters = sum(1 for ch in sample if ch.isalpha())
    arabic = sum(1 for ch in sample if "؀" <= ch <= "ۿ")
    if letters and arabic / letters > 0.3:
        return "Arabic"
    words = _WORD.findall(sample.lower())
    en = sum(1 for w in words if w in _EN)
    idn = sum(1 for w in words if w in _ID)
    if en >= 5 and en > idn * 1.5:
        return "English"
    return DEFAULT_LANGUAGE
