"""ZIP code → lat/lon for SMS users who cannot share a location pin."""
import re

from app.data.http import get_json


def zip_to_point(text: str) -> tuple[float, float, str] | None:
    m = re.search(r"\b(\d{5})\b", text or "")
    if not m:
        return None
    try:
        d = get_json(f"https://api.zippopotam.us/us/{m.group(1)}", ttl_s=0)
    except Exception:
        return None
    p = d["places"][0]
    return float(p["latitude"]), float(p["longitude"]), f"{p['place name']}, {p['state abbreviation']}"

STATES = {"01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT", "10": "DE", "12": "FL",
          "13": "GA", "16": "ID", "17": "IL", "18": "IN", "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME",
          "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS", "29": "MO", "30": "MT", "31": "NE", "32": "NV",
          "33": "NH", "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH", "40": "OK", "41": "OR",
          "42": "PA", "44": "RI", "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA",
          "53": "WA", "54": "WV", "55": "WI", "56": "WY"}


def county_name(lat: float, lon: float) -> str | None:
    """U.S. Census reverse geocoder → 'Finney County, KS'. None outside the U.S. or on error."""
    try:
        d = get_json("https://geocoding.geo.census.gov/geocoder/geographies/coordinates",
                     {"x": round(lon, 4), "y": round(lat, 4), "benchmark": "Public_AR_Current",
                      "vintage": "Current_Current", "layers": "Counties", "format": "json"}, ttl_s=0, timeout=20)
        c = d["result"]["geographies"]["Counties"][0]
        return f"{c['NAME']}, {STATES.get(c['STATE'], c['STATE'])}"
    except Exception:
        return None
