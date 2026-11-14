"""Pre-fetch every data source for the demo farms so the live demo never waits on slow public APIs.

    uv run python scripts/warm_cache.py
"""
import time

from app.engine.features import build_features

DEMO_FARMS = {"Garden City, KS (dryland wheat–sorghum–fallow)": (38.05, -100.75),
              "Hastings, NE (irrigated continuous corn)": (40.65, -98.30),
              "Hastings, NE (corn–soybeans)": (40.55, -98.40)}

for name, (lat, lon) in DEMO_FARMS.items():
    t = time.time()
    f = build_features(lat, lon, with_ndvi=True)
    print(f"{name}: {time.time() - t:.0f}s, sources={len(f['sources'])}, errors={f['errors']}")
