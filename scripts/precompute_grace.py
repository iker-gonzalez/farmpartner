"""Pre-compute the NASA GRACE/GRACE-FO water-storage trend for every 0.5° cell over the contiguous U.S.

Source: JPL GRACE & GRACE-FO Mascon RL06.3 CRI (TELLUS_GRAC-GRFO_MASCON_CRI_GRID_RL06.3_V4), monthly
liquid-water-equivalent thickness anomaly (cm). Output: data/grace_trends.csv (lat, lon, trend_cm_per_yr, period),
read at runtime by app/data/grace.py. Total water storage includes groundwater, soil moisture, snow and surface water.

    ./scripts/get-grace.sh                       # downloads with an Earthdata token from the clipboard
    uv run --with xarray --with netCDF4 python scripts/precompute_grace.py path/to/file.nc
"""
import csv
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

import numpy as np
import xarray as xr

URL = ("https://archive.podaac.earthdata.nasa.gov/podaac-ops-cumulus-protected/"
       "TELLUS_GRAC-GRFO_MASCON_CRI_GRID_RL06.3_V4/GRCTellus.JPL.200204_202609.GLO.RL06.3M.MSCNv04CRI.nc")
OUT = Path(__file__).resolve().parent.parent / "data" / "grace_trends.csv"
LAT, LON360 = (24.0, 50.0), (235.0, 294.0)  # contiguous U.S. (lon 0-360)


def download() -> Path:
    token = os.environ.get("EARTHDATA_TOKEN", "").strip()
    if not token:
        sys.exit("Set EARTHDATA_TOKEN (scripts/get-grace.sh reads it from the clipboard).")
    dest = Path(tempfile.gettempdir()) / "grace_mascon_cri.nc"
    if dest.exists() and dest.stat().st_size > 40_000_000:
        return dest
    req = urllib.request.Request(URL, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=300) as r, open(dest, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    return dest


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else download()
    ds = xr.open_dataset(path)
    lwe = ds["lwe_thickness"].sel(lat=slice(*LAT), lon=slice(*LON360))
    if "land_mask" in ds:
        lwe = lwe.where(ds["land_mask"].sel(lat=slice(*LAT), lon=slice(*LON360)) > 0)
    times = lwe["time"].values.astype("datetime64[D]")
    t = (times - times[0]).astype(float) / 365.25                     # years since first month
    y = lwe.values.reshape(len(t), -1)                                 # (time, cells)
    ok = np.isfinite(y)
    n = ok.sum(0)
    tt = np.where(ok, t[:, None], np.nan)
    tm, ym = np.nanmean(tt, 0), np.nanmean(np.where(ok, y, np.nan), 0)
    slope = np.nansum((tt - tm) * (y - ym), 0) / np.nansum((tt - tm) ** 2, 0)   # cm per year, gaps tolerated
    lats, lons = np.meshgrid(lwe["lat"].values, lwe["lon"].values, indexing="ij")
    period = f"{str(times[0])[:4]}–{str(times[-1])[:4]}"
    OUT.parent.mkdir(exist_ok=True)
    rows = 0
    with OUT.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["lat", "lon", "trend_cm_per_yr", "period"])
        for la, lo, s, k in zip(lats.ravel(), lons.ravel(), slope.ravel(), n.ravel()):
            if np.isfinite(s) and k >= 60:  # at least 5 years of months
                w.writerow([round(float(la), 2), round(float(lo) - 360, 2), round(float(s), 2), period])
                rows += 1
    print(f"wrote {rows} cells to {OUT} ({period}, {len(t)} months)")


if __name__ == "__main__":
    main()
