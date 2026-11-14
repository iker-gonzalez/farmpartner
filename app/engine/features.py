"""Turn raw NASA / USDA data into a compact, explainable feature set for one field."""
import datetime as dt
import math
import statistics as st
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

from app.data import cdl, forecast, grace, modis, power, soil
from app.engine.knowledge import crop_from_cdl, crops

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _ra_mm(lat: float, doy: int) -> float:
    """Extraterrestrial radiation (FAO-56 eq. 21) converted to mm/day of evaporation."""
    phi = math.radians(lat)
    dr = 1 + 0.033 * math.cos(2 * math.pi * doy / 365)
    delta = 0.409 * math.sin(2 * math.pi * doy / 365 - 1.39)
    ws = math.acos(max(-1.0, min(1.0, -math.tan(phi) * math.tan(delta))))
    ra = 24 * 60 / math.pi * 0.0820 * dr * (ws * math.sin(phi) * math.sin(delta)
                                             + math.cos(phi) * math.cos(delta) * math.sin(ws))
    return 0.408 * ra


def et0_hargreaves(lat: float, d: dt.date, tmax: float, tmin: float) -> float:
    tmean = (tmax + tmin) / 2
    return max(0.0, 0.0023 * _ra_mm(lat, d.timetuple().tm_yday) * (tmean + 17.8) * math.sqrt(max(tmax - tmin, 0)))


def climate_features(lat: float, rows: list[dict]) -> dict:
    by_year = defaultdict(list)
    for r in rows:
        if r["tmax"] is not None and r["tmin"] is not None:
            r["et0"] = et0_hargreaves(lat, r["date"], r["tmax"], r["tmin"])
        else:
            r["et0"] = None
        by_year[r["date"].year].append(r)
    full_years = sorted(y for y, rs in by_year.items() if len(rs) >= 360)

    yearly = []
    for y in full_years:
        rs = by_year[y]
        spring = [r for r in rs if r["date"].month < 7 and r["tmin"] is not None and r["tmin"] <= 0]
        fall = [r for r in rs if r["date"].month >= 7 and r["tmin"] is not None and r["tmin"] <= 0]
        last_spring = spring[-1]["date"].timetuple().tm_yday if spring else None
        first_fall = fall[0]["date"].timetuple().tm_yday if fall else None
        season = [r for r in rs if r["tmax"] is not None and last_spring and first_fall
                  and last_spring < r["date"].timetuple().tm_yday < first_fall]
        gdd = sum(max(0.0, (min(r["tmax"], 30) + max(r["tmin"], 10)) / 2 - 10) for r in season)
        yearly.append({
            "year": y,
            "precip_mm": sum(r["precip"] or 0 for r in rs),
            "et0_mm": sum(r["et0"] or 0 for r in rs),
            "heat_days": sum(1 for r in rs if (r["tmax"] or 0) >= 35),
            "heavy_rain_days": sum(1 for r in rs if (r["precip"] or 0) >= 25),
            "last_spring_frost_doy": last_spring,
            "first_fall_frost_doy": first_fall,
            "frost_free_days": (first_fall - last_spring) if last_spring and first_fall else None,
            "season_gdd_c": gdd,
        })

    monthly = defaultdict(lambda: {"precip": [], "et0": [], "gwet": []})
    for y in full_years:
        per_month = defaultdict(lambda: {"precip": 0.0, "et0": 0.0, "gwet": []})
        for r in by_year[y]:
            m = r["date"].month
            per_month[m]["precip"] += r["precip"] or 0
            per_month[m]["et0"] += r["et0"] or 0
            if r["gwet"] is not None:
                per_month[m]["gwet"].append(r["gwet"])
        for m, v in per_month.items():
            monthly[m]["precip"].append(v["precip"])
            monthly[m]["et0"].append(v["et0"])
            if v["gwet"]:
                monthly[m]["gwet"].append(st.mean(v["gwet"]))
    clim = [{"month": MONTHS[m - 1], "precip_mm": round(st.mean(monthly[m]["precip"]), 1),
             "et0_mm": round(st.mean(monthly[m]["et0"]), 1),
             "soil_wetness": round(st.mean(monthly[m]["gwet"]), 3) if monthly[m]["gwet"] else None}
            for m in range(1, 13) if monthly[m]["precip"]]

    def med(key):
        vals = [y[key] for y in yearly if y[key] is not None]
        return st.median(vals) if vals else None

    def mean_of(key, ys):
        vals = [y[key] for y in ys if y[key] is not None]
        return st.mean(vals) if vals else None

    early, late = yearly[: len(yearly) // 2], yearly[-5:]
    last365 = rows[-365:]
    recent_precip = sum(r["precip"] or 0 for r in last365)
    normal_precip = mean_of("precip_mm", yearly)

    # Root-zone soil wetness: last 60 days vs the same calendar window in all prior years
    recent = [r for r in rows[-60:] if r["gwet"] is not None]
    if recent:
        doys = {r["date"].timetuple().tm_yday for r in recent}
        hist = [r["gwet"] for r in rows[:-365] if r["gwet"] is not None and r["date"].timetuple().tm_yday in doys]
        gwet_now, gwet_norm = st.mean(r["gwet"] for r in recent), (st.mean(hist) if hist else None)
    else:
        gwet_now = gwet_norm = None

    def doy_to_date(doy):
        return (dt.date(2001, 1, 1) + dt.timedelta(days=int(doy) - 1)).strftime("%b %d") if doy else None

    return {
        "years": f"{full_years[0]}–{full_years[-1]}" if full_years else None,
        "annual_precip_mm": round(normal_precip, 0) if normal_precip else None,
        "annual_et0_mm": round(mean_of("et0_mm", yearly), 0) if yearly else None,
        "water_balance_mm": round(normal_precip - mean_of("et0_mm", yearly), 0) if yearly else None,
        "last_12mo_precip_mm": round(recent_precip, 0),
        "last_12mo_precip_pct_normal": round(100 * recent_precip / normal_precip) if normal_precip else None,
        "median_last_spring_frost": doy_to_date(med("last_spring_frost_doy")),
        "median_first_fall_frost": doy_to_date(med("first_fall_frost_doy")),
        "frost_free_days": med("frost_free_days"),
        "season_gdd_c": round(med("season_gdd_c") or 0),
        "heat_days_per_yr": round(mean_of("heat_days", yearly), 1) if yearly else None,
        "heat_days_early": round(mean_of("heat_days", early), 1) if early else None,
        "heat_days_recent5": round(mean_of("heat_days", late), 1) if late else None,
        "heavy_rain_days_per_yr": round(mean_of("heavy_rain_days", yearly), 1) if yearly else None,
        "soil_wetness_now": round(gwet_now, 3) if gwet_now is not None else None,
        "soil_wetness_normal": round(gwet_norm, 3) if gwet_norm is not None else None,
        "monthly": clim,
        "yearly": [{k: (round(v, 1) if isinstance(v, float) else v) for k, v in y.items()} for y in yearly],
    }


def ndvi_features(series: list[dict]) -> dict:
    by_month = defaultdict(list)
    for s in series:
        by_month[int(s["date"][5:7])].append(s["ndvi"])
    monthly = {m: round(st.mean(v), 3) for m, v in sorted(by_month.items())}
    bare = [MONTHS[m - 1] for m, v in monthly.items() if v < 0.25]
    return {"monthly_ndvi": monthly, "bare_months": bare, "peak_ndvi": max(monthly.values()) if monthly else None,
            "series": series}


def detect_rotation(history: list[dict]) -> dict:
    seq = [(h["year"], crop_from_cdl(h["category"])) for h in history]
    sequence = [{"year": y, "crop": c, "category": h["category"]} for (y, c), h in zip(seq, history)]
    known = [(y, c) for y, c in seq if c]
    if len(known) < 3:
        return {"is_cropland": False, "sequence": sequence, "pattern": []}
    ids = [c for _, c in known][-6:]
    def phase_mode(p):
        """Most common crop at each position of a p-year cycle, aligned so the cycle ends on the latest year."""
        n = len(ids)
        return [Counter(ids[i] for i in range(n) if (n - 1 - i) % p == p - 1 - j).most_common(1)[0][0]
                for j in range(p)]

    pattern = ids[-3:]
    for p in range(1, 5):
        cand = phase_mode(p)
        n = len(ids)
        mism = sum(1 for i in range(n) if ids[i] != cand[(i - (n - p)) % p])
        if mism <= max(1, n // 5):
            pattern = cand
            break
    # Continuous single crop shows as period 1; keep two entries so scoring sees the repeat.
    if len(pattern) == 1:
        pattern = pattern * 2
    return {"is_cropland": True, "sequence": sequence, "recent": ids, "pattern": pattern}


def build_features(lat: float, lon: float, with_ndvi: bool = True) -> dict:
    """Fetch every source in parallel. MODIS is the slow one (~1 min); pass with_ndvi=False for a fast first
    answer and call add_ndvi() later."""
    out = {"lat": lat, "lon": lon, "built_at": dt.datetime.utcnow().isoformat(timespec="seconds"),
           "sources": [], "errors": [], "ndvi": None}
    jobs = {
        "power": lambda: climate_features(lat, power.to_rows(power.fetch_daily(lat, lon))),
        "cdl": lambda: cdl.crop_history(lat, lon),
        "soil": lambda: soil.soil_at(lat, lon),
        "grace": lambda: grace.tws_trend(lat, lon),
        "elev": lambda: forecast.elevation(lat, lon),
    }
    if with_ndvi:
        jobs["ndvi"] = lambda: ndvi_features(modis.fetch_ndvi(lat, lon))
    with ThreadPoolExecutor(max_workers=len(jobs)) as ex:
        futs = {k: ex.submit(fn) for k, fn in jobs.items()}
        res = {}
        for k, f in futs.items():
            try:
                res[k] = f.result()
            except Exception as e:  # every source is optional; failures become information gaps
                res[k] = None
                out["errors"].append(f"{k}: {e}")
    out["climate"] = res["power"]
    if res["power"]:
        out["sources"].append("NASA POWER daily (MERRA-2 temperature, bias-corrected precipitation, GWETROOT soil wetness)")
    out["rotation"] = detect_rotation(res["cdl"] or [])
    if res["cdl"]:
        out["sources"].append("USDA NASS Cropland Data Layer 30 m (CropScape)")
    out["soil"] = res["soil"]
    if res["soil"]:
        out["sources"].append("USDA NRCS SSURGO soil survey (Soil Data Access)")
    out["grace"] = res["grace"]
    if res["grace"]:
        out["sources"].append("NASA GRACE/GRACE-FO terrestrial water storage")
    out["elevation_m"] = res["elev"]
    if with_ndvi:
        _set_ndvi(out, res.get("ndvi"))
    return out


def _set_ndvi(out: dict, ndvi: dict | None):
    out["ndvi"] = ndvi
    src = "NASA MODIS MOD13Q1 NDVI 250 m (ORNL DAAC)"
    if ndvi and src not in out["sources"]:
        out["sources"].append(src)


def add_ndvi(features: dict) -> dict:
    try:
        _set_ndvi(features, ndvi_features(modis.fetch_ndvi(features["lat"], features["lon"])))
    except Exception as e:
        features["errors"].append(f"ndvi: {e}")
    return features


def crop_name(cid: str) -> str:
    return crops().get(cid, {}).get("name", cid)


def rotation_label(ids: list[str]) -> str:
    if ids and len(set(ids)) == 1:
        return f"Continuous {crop_name(ids[0]).lower()}"
    return " → ".join(crop_name(c) for c in ids)
