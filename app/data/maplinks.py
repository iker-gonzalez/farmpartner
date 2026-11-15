"""Locations typed or pasted as text: "36.31, -98.11" or a Google / Apple Maps link (short links are resolved).

Only known map hosts are fetched, and only to read their redirect, so a pasted link can't make us call anywhere else.
"""
import re
from urllib.parse import unquote, urlparse

import httpx

MAP_HOSTS = ("maps.app.goo.gl", "goo.gl", "maps.google.com", "www.google.com", "google.com", "maps.apple.com",
             "maps.apple")
_NUM_LAT, _NUM_LON = r"(-?\d{1,2}(?:\.\d+))", r"(-?\d{1,3}(?:\.\d+))"
_PAIR = _NUM_LAT + r"\s*,\s*" + _NUM_LON
_PATTERNS = [r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)",          # Google place data (most precise)
             r"[?&](?:q|ll|query|sll|daddr|center)=" + _PAIR,
             r"@" + _PAIR, r"/place/" + _PAIR, r"/search/" + _PAIR]


def _valid(lat: float, lon: float) -> tuple[float, float] | None:
    return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 and (lat, lon) != (0.0, 0.0) else None


def _from_url(url: str) -> tuple[float, float] | None:
    u = unquote(url)
    for pat in _PATTERNS:
        m = re.search(pat, u)
        if m:
            return _valid(float(m.group(1)), float(m.group(2)))
    return None


def _resolve(url: str, hops: int = 4) -> str:
    """Follow redirects of a short map link, staying on map hosts."""
    with httpx.Client(timeout=6, follow_redirects=False) as c:
        for _ in range(hops):
            if _from_url(url):
                return url
            r = c.get(url)
            nxt = r.headers.get("location")
            if not (r.is_redirect and nxt):
                return url
            nxt = httpx.URL(url).join(nxt)
            if (nxt.host or "") not in MAP_HOSTS:
                return url
            url = str(nxt)
    return url


_DMS = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*°\s*(?:(\d{1,2}(?:\.\d+)?)\s*['′’]\s*)?(?:(\d{1,2}(?:\.\d+)?)\s*(?:\"|″|''|”)?\s*)?"
                  r"([NSEWnsewO])?")


def _from_dms(text: str) -> tuple[float, float] | None:
    """31°45'12.0"N 42°18'34.0"E · 31° 45' N, 2° 10' W · 41.39°N 2.17°E (a missing letter means N / E)."""
    parts = [m for m in _DMS.finditer(text) if m.group(0).strip()]
    if len(parts) < 2:
        return None
    vals = []
    for m in parts[:2]:
        v = float(m.group(1)) + float(m.group(2) or 0) / 60 + float(m.group(3) or 0) / 3600
        if (m.group(4) or "").upper() in ("S", "W", "O"):  # O = oeste
            v = -v
        vals.append(v)
    a, b = vals
    if (parts[0].group(4) or "").upper() in ("E", "W", "O"):  # longitude written first
        a, b = b, a
    return _valid(a, b)


def location_from_text(text: str) -> tuple[float, float] | None:
    t = (text or "").strip()
    for url in re.findall(r"https?://\S+", t):
        if (urlparse(url).hostname or "") in MAP_HOSTS:
            try:
                pt = _from_url(url) or _from_url(_resolve(url))
            except Exception:
                pt = None
            if pt:
                return pt
    if "°" in t:
        pt = _from_dms(t)
        if pt:
            return pt
    m = re.fullmatch(r"\s*" + _PAIR + r"\s*", t.replace(";", ","))
    if not m:  # coordinates with words around them ("43.416425, -2.914102 ahora no tengo cultivos"): only precise
        m = re.search(r"(?<![\d.])(-?\d{1,2}\.\d{3,})\s*[,;]\s*(-?\d{1,3}\.\d{3,})(?![\d.])", t)  # ones, not "1.5, 2.0"
    return _valid(float(m.group(1)), float(m.group(2))) if m else None
