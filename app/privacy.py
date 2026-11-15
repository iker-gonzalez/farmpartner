"""Remove direct identifiers from farmers' text before it goes to Claude.

The field is already known to Claude by its place name; phone numbers, e-mail addresses, exact coordinates and map
links typed into a message add nothing to the advice, so they are replaced with a neutral label.
"""
import re

_MAP_LINK = re.compile(r"https?://(?:maps\.app\.goo\.gl|goo\.gl/maps|(?:www\.)?google\.[a-z.]+/maps|maps\.google\.[a-z.]+|"
                       r"maps\.apple(?:\.com)?)\S*", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# precise coordinates only (3+ decimals), so "1.5, 2.0 inches" stays
_COORDS = re.compile(r"(?<![\d.])-?\d{1,3}\.\d{3,}\s*[,;]\s*-?\d{1,3}\.\d{3,}(?![\d.])")
_DMS = re.compile(r"\d{1,3}\s*°\s*\d{1,2}\s*['′’]\s*(?:\d{1,2}(?:\.\d+)?\s*[\"″”]?\s*)?[NSns]?[,\s]+"
                  r"\d{1,3}\s*°\s*\d{1,2}\s*['′’]\s*(?:\d{1,2}(?:\.\d+)?\s*[\"″”]?\s*)?[EWOewo]?")
# phone numbers: 9+ digits, optionally with +, spaces, dots, dashes or brackets (dates like 2026-11-15 have 8)
_PHONE = re.compile(r"(?<![\w.,])\+?\(?\d[\d\s().-]{7,}\d(?![\w])")


def _phone(m: re.Match) -> str:
    return "[phone number]" if len(re.sub(r"\D", "", m.group(0))) >= 9 else m.group(0)


def scrub(text: str) -> str:
    if not text:
        return text
    t = _MAP_LINK.sub("[map link]", text)
    t = _EMAIL.sub("[email]", t)
    t = _COORDS.sub("[coordinates]", t)
    t = _DMS.sub("[coordinates]", t)
    return _PHONE.sub(_phone, t)
