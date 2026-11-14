"""USDA NASS Cropland Data Layer (CropScape) point lookups: what was grown at this pixel each year (U.S. only)."""
import re
from concurrent.futures import ThreadPoolExecutor

from pyproj import Transformer

from app.data.http import get_text

URL = "https://nassgeodata.gmu.edu/axis2/services/CDLService/GetCDLValue"
_to_albers = Transformer.from_crs(4326, 5070, always_xy=True)
YEARS = list(range(2017, 2026))


def _one(year: int, x: float, y: float) -> tuple[int, str | None]:
    txt = None
    for _ in range(2):  # the CropScape server drops requests now and then; retry once
        try:
            txt = get_text(URL, {"year": year, "x": round(x, 1), "y": round(y, 1)}, ttl_s=0,
                           keep=lambda t: "category:" in t)  # never cache CropScape's error pages
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
    with ThreadPoolExecutor(max_workers=len(YEARS)) as ex:
        results = list(ex.map(lambda yr: _one(yr, x, y), YEARS))
    return [{"year": yr, "category": cat} for yr, cat in sorted(results) if cat]
