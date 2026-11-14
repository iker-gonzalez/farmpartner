"""GRACE/GRACE-FO terrestrial water storage trend.

GRACE mascons need an Earthdata login and are ~3° effective resolution, so we pre-compute a small
regional table with scripts/precompute_grace.py into data/grace_trends.csv (lat,lon,trend_cm_per_yr,period).
If the file is missing we report it as an information gap rather than inventing a number.
"""
import csv
import math

from app.config import ROOT

PATH = ROOT / "data" / "grace_trends.csv"


def tws_trend(lat: float, lon: float) -> dict | None:
    if not PATH.exists():
        return None
    best, best_d = None, 1e9
    with PATH.open() as f:
        for row in csv.DictReader(f):
            d = math.hypot(float(row["lat"]) - lat, float(row["lon"]) - lon)
            if d < best_d:
                best, best_d = row, d
    if not best or best_d > 1.5:
        return None
    return {"trend_cm_per_yr": float(best["trend_cm_per_yr"]), "period": best.get("period", "")}
