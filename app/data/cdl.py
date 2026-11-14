"""USDA NASS Cropland Data Layer (CropScape) point lookups: what was grown at this pixel each year (U.S. only)."""
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, wait

from pyproj import Transformer

from app.data.http import get_text

URL = "https://nassgeodata.gmu.edu/axis2/services/CDLService/GetCDLValue"
_to_albers = Transformer.from_crs(4326, 5070, always_xy=True)
YEARS = list(range(2017, 2026))
log = logging.getLogger(__name__)
REQUEST_TIMEOUT_S = 10  # CropScape sometimes hangs; a missing year is fine, a 2-minute wait is not
DEADLINE_S = 25


def _one(year: int, x: float, y: float, until: float) -> tuple[int, str | None]:
    txt = None
    for _ in range(2):  # the CropScape server drops requests now and then; retry once
        if time.monotonic() > until:
            break
        try:
            txt = get_text(URL, {"year": year, "x": round(x, 1), "y": round(y, 1)}, ttl_s=0,
                           keep=lambda t: "category:" in t, timeout=REQUEST_TIMEOUT_S)  # never cache CropScape's error pages
            break
        except Exception:
            continue
    if txt is None:
        return year, None
    m = re.search(r'category: "([^"]*)"', txt)
    return year, (m.group(1) if m else None)


def crop_history(lat: float, lon: float) -> list[dict]:
    if not (24 <= lat <= 50 and -125 <= lon <= -66):
        return []  # CDL covers the contiguous U.S. only
    x, y = _to_albers.transform(lon, lat)
    until = time.monotonic() + DEADLINE_S
    ex = ThreadPoolExecutor(max_workers=len(YEARS))
    futures = [ex.submit(_one, yr, x, y, until) for yr in YEARS]
    done, late = wait(futures, timeout=DEADLINE_S + 2)
    ex.shutdown(wait=False, cancel_futures=True)  # don't block on a hung request; it times out on its own
    if late:
        log.warning("CropScape: %d of %d years timed out; continuing without them", len(late), len(YEARS))
    results = [f.result() for f in done]
    return [{"year": yr, "category": cat} for yr, cat in sorted(results) if cat]

