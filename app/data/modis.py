"""MODIS MOD13Q1 NDVI (250 m, 16-day) via the ORNL DAAC fixed-site subset REST API (no login)."""
import datetime as dt
from concurrent.futures import ThreadPoolExecutor

from app.data.http import get_json

BASE = "https://modis.ornl.gov/rst/api/v1/MOD13Q1"
HDR = {"Accept": "application/json"}


def fetch_ndvi(lat: float, lon: float, years: int = 2) -> list[dict]:
    dates = get_json(f"{BASE}/dates", {"latitude": round(lat, 4), "longitude": round(lon, 4)},
                     ttl_s=30 * 86400, headers=HDR)["dates"]
    cutoff = dt.date.today().replace(year=dt.date.today().year - years)
    # The API returns at most 10 composites per call. Chunk on fixed positions in the full date list, so
    # past chunks keep the same request (and cache key) as new composites arrive every 16 days.
    by_chunk: dict[int, list] = {}
    for i, d in enumerate(dates):
        if dt.date.fromisoformat(d["calendar_date"]) >= cutoff:
            by_chunk.setdefault(i // 10, []).append(d)
    chunks = [by_chunk[k] for k in sorted(by_chunk)]

    def one(chunk, attempt=0):
        try:
            return get_json(f"{BASE}/subset", {
                "latitude": round(lat, 4), "longitude": round(lon, 4), "band": "250m_16_days_NDVI",
                "startDate": chunk[0]["modis_date"], "endDate": chunk[-1]["modis_date"],
                "kmAboveBelow": 0, "kmLeftRight": 0,
            }, ttl_s=0, headers=HDR, timeout=180, keep=lambda r: bool(r.get("subset"))).get("subset", [])
        except Exception:
            return one(chunk, 1) if attempt == 0 else []

    with ThreadPoolExecutor(max_workers=3) as ex:
        subsets = list(ex.map(one, chunks))
    out = []
    for subset in subsets:
        for s in subset:
            raw = s["data"][0] if s.get("data") else None
            if raw is None or raw <= -3000:
                continue
            out.append({"date": s["calendar_date"], "ndvi": round(raw * 0.0001, 3)})
    if not out:
        raise RuntimeError("no NDVI composites returned")
    return sorted(out, key=lambda x: x["date"])
