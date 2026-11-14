"""NASA AppEEARS point extraction for SMAP L4 soil moisture (9 km) and ECOSTRESS evaporative stress (70 m).

AppEEARS works asynchronously: submit a point task, then collect results once it is "done" (minutes to an hour).
The app submits at onboarding and collects in the background and in the daily job (service.collect_satellite).

Auth: an Earthdata token (EARTHDATA_TOKEN) if AppEEARS accepts it, otherwise username/password login, which
returns a 48 h session token.
"""
import csv
import datetime as dt
import io
import logging
import time

import httpx

from app import config

log = logging.getLogger(__name__)
API = "https://appeears.earthdatacloud.nasa.gov/api"
SMAP = "SPL4SMGP.008"
ECO = "ECO_L4T_ESI.002"
SMAP_LAYERS = [
    {"product": SMAP, "layer": "Geophysical_Data_sm_rootzone"},
    {"product": SMAP, "layer": "Geophysical_Data_sm_rootzone_pctl"},
    {"product": SMAP, "layer": "Geophysical_Data_sm_surface"},
]
ECO_LAYERS = [{"product": ECO, "layer": "ESI"}]
# Separate jobs: SMAP (gridded, fast) must not wait behind ECOSTRESS (many 70 m scenes, slow).
JOBS = {"smap": (SMAP_LAYERS, 120), "eco": (ECO_LAYERS, 365)}
_session = {"token": None, "expires": 0.0}
_http = httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0), follow_redirects=True)


def enabled() -> bool:
    return bool(config.EARTHDATA_TOKEN or (config.EARTHDATA_USERNAME and config.EARTHDATA_PASSWORD))


def _headers() -> dict:
    if config.EARTHDATA_TOKEN:
        return {"Authorization": f"Bearer {config.EARTHDATA_TOKEN}"}
    if not _session["token"] or time.time() > _session["expires"] - 3600:
        r = _http.post(f"{API}/login", auth=(config.EARTHDATA_USERNAME, config.EARTHDATA_PASSWORD),
                       headers={"Content-Length": "0"})
        r.raise_for_status()
        _session["token"], _session["expires"] = r.json()["token"], time.time() + 47 * 3600
    return {"Authorization": f"Bearer {_session['token']}"}


def submit(lat: float, lon: float, name: str, layers: list[dict], days: int) -> str:
    end = dt.date.today()
    start = end - dt.timedelta(days=days)
    task = {
        "task_type": "point",
        "task_name": name[:100],
        "params": {
            "dates": [{"startDate": start.strftime("%m-%d-%Y"), "endDate": end.strftime("%m-%d-%Y")}],
            "layers": layers,
            "coordinates": [{"latitude": round(lat, 5), "longitude": round(lon, 5), "id": "field"}],
        },
    }
    r = _http.post(f"{API}/task", json=task, headers=_headers())
    r.raise_for_status()
    return r.json()["task_id"]


def status(task_id: str) -> str:
    """queued | pending | processing | done | error | expired | deleted"""
    r = _http.get(f"{API}/task/{task_id}", headers=_headers())
    if r.status_code == 404:
        return "deleted"
    r.raise_for_status()
    return r.json().get("status", "unknown")


def results(task_id: str) -> dict[str, list[dict]]:
    """Result CSV rows per product, keyed by product id."""
    r = _http.get(f"{API}/bundle/{task_id}", headers=_headers())
    r.raise_for_status()
    out: dict[str, list[dict]] = {}
    for f in r.json().get("files", []):
        name = f.get("file_name", "")
        if not name.endswith("results.csv"):
            continue
        product = SMAP if "SPL4SMGP" in name else ECO if "ECO" in name else None
        if not product:
            continue
        data = _http.get(f"{API}/bundle/{task_id}/{f['file_id']}", headers=_headers())
        data.raise_for_status()
        out[product] = list(csv.DictReader(io.StringIO(data.text)))
    return out


def _col(row: dict, suffix: str) -> float | None:
    for k, v in row.items():
        if k.endswith(suffix):
            try:
                x = float(v)
            except (TypeError, ValueError):
                return None
            return None if x != x or x <= -9999 else x  # NaN / fill
    return None


def smap_features(rows: list[dict]) -> dict | None:
    """Last-30-day root-zone soil moisture, its percentile vs SMAP's own climatology, and a monthly series."""
    pts = []
    for r in rows:
        d = (r.get("Date") or "")[:10]
        rz, pct, sf = (_col(r, "_sm_rootzone"), _col(r, "_sm_rootzone_pctl"), _col(r, "_sm_surface"))
        if d and rz is not None:
            pts.append((d, rz, pct, sf))
    if not pts:
        return None
    pts.sort()
    last = dt.date.fromisoformat(pts[-1][0])
    recent = [p for p in pts if (last - dt.date.fromisoformat(p[0])).days <= 30]
    mean = lambda xs: round(sum(xs) / len(xs), 3) if xs else None  # noqa: E731
    monthly: dict[str, list[float]] = {}
    for d, rz, _, _ in pts:
        monthly.setdefault(d[:7], []).append(rz)
    return {
        "as_of": pts[-1][0],
        "rootzone_m3m3": mean([p[1] for p in recent]),
        "rootzone_percentile": mean([p[2] for p in recent if p[2] is not None]),
        "surface_m3m3": mean([p[3] for p in recent if p[3] is not None]),
        "monthly_rootzone": {k: mean(v) for k, v in sorted(monthly.items())},
    }


def ecostress_features(rows: list[dict]) -> dict | None:
    """Field-scale (70 m) Evaporative Stress Index: actual / potential evapotranspiration (low = water-stressed)."""
    obs = []
    for r in rows:
        d = (r.get("Date") or "")[:10]
        esi = _col(r, "_ESI")
        if d and esi is not None and 0 <= esi <= 1.5:
            obs.append((d, min(esi, 1.0)))
    if not obs:
        return None
    obs.sort()
    summer = [e for d, e in obs if d[5:7] in ("06", "07", "08")]
    return {
        "observations": len(obs),
        "latest": {"date": obs[-1][0], "esi": round(obs[-1][1], 2)},
        "summer_mean_esi": round(sum(summer) / len(summer), 2) if summer else None,
        "stressed_share": round(sum(1 for _, e in obs if e < 0.4) / len(obs), 2),
        "series": [{"date": d, "esi": round(e, 2)} for d, e in obs[-60:]],
    }
