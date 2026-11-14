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
    m = re.fullmatch(r"\s*" + _PAIR + r"\s*", t.replace(";", ","))
    return _valid(float(m.group(1)), float(m.group(2))) if m else None
