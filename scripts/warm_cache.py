"""Pre-fetch every data source for the demo locations into the cache, so the live demo never waits on slow
public APIs. With CACHE_BUCKET set (the deployed bucket), Cloud Run reads the same entries.

    CACHE_DIR=$(mktemp -d) CACHE_BUCKET=$(terraform -chdir=terraform output -raw cache_bucket) PYTHONPATH=. uv run python scripts/warm_cache.py

Use an empty CACHE_DIR: local cache hits are never uploaded to the bucket.
"""
import sys
import time

from app.config import CACHE_BUCKET
from app.data import forecast
from app.data.geocode import county_name, zip_to_point
from app.engine.features import build_features

DEMO_FARMS = {
    "Garden City, KS (demo pin, dryland wheat-sorghum-fallow)": (38.05, -100.75),
    "Hastings, NE (demo pin, irrigated corn)": (40.65, -98.30),
    "Hastings, NE (corn-soybeans)": (40.55, -98.40),
}
DEMO_ZIPS = ["67846", "68901"]  # simulator / SMS ZIP demos (Garden City KS, Hastings NE)

if __name__ == "__main__":
    print("cache bucket:", CACHE_BUCKET or "(none: local cache only)")
    points = dict(DEMO_FARMS)
    for z in DEMO_ZIPS:
        p = zip_to_point(z)
        if p:
            points[f"ZIP {z} ({p[2]})"] = (p[0], p[1])
    for extra in sys.argv[1:]:  # more points: "lat,lon"
        lat, lon = map(float, extra.split(","))
        points[f"extra {extra}"] = (lat, lon)
    for name, (lat, lon) in points.items():
        t = time.time()
        f = build_features(lat, lon, with_ndvi=True)
        county_name(lat, lon)
        forecast.forecast(lat, lon, 7)
        rot = (f.get("rotation") or {}).get("pattern")
        print(f"{name}: {time.time() - t:.0f}s, sources={len(f['sources'])}, ndvi={'yes' if f.get('ndvi') else 'NO'}, "
              f"rotation={rot}, errors={f['errors']}", flush=True)
