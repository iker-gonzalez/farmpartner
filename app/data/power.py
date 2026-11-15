"""NASA POWER (power.larc.nasa.gov): ~20 years of daily agro-climate at a point, worldwide.

Parameters: T2M_MAX/T2M_MIN (MERRA-2), PRECTOTCORR (bias-corrected precipitation, IMERG-informed),
GWETROOT (root-zone soil wetness, 0-1, from NASA GMAO MERRA-2 land model).
"""
import datetime as dt

from app.data.http import get_json

URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
PARAMS = "T2M_MAX,T2M_MIN,PRECTOTCORR,GWETROOT"
FILL = -999.0


def grid_lat(lat: float) -> float:
    """Centre of the MERRA-2 cell (0.5° lat x 0.625° lon) holding the point. POWER answers per cell, so this returns
    the same data as the exact point, sends NASA only the ~50 km cell, and lets nearby fields share one cached copy."""
    return round(round(lat / 0.5) * 0.5, 4)


def grid_lon(lon: float) -> float:
    return round(round(lon / 0.625) * 0.625, 4)


def fetch_daily(lat: float, lon: float, years: int = 20) -> dict[str, dict[str, float]]:
    # POWER lags a few days. End on the last Sunday at least 7 days ago, so the request (and its cache key)
    # stays the same for a week instead of changing every day.
    end = dt.date.today() - dt.timedelta(days=7)
    end -= dt.timedelta(days=(end.weekday() + 1) % 7)
    start = dt.date(end.year - years, 1, 1)
    data = get_json(URL, {
        "parameters": PARAMS, "community": "AG", "latitude": grid_lat(lat), "longitude": grid_lon(lon),
        "start": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"), "format": "JSON",
    }, ttl_s=7 * 86400)
    return data["properties"]["parameter"]


def to_rows(params: dict[str, dict[str, float]]) -> list[dict]:
    """Return daily rows: {date, tmax, tmin, precip, gwet}; fill values become None."""
    rows = []
    for key in sorted(params["T2M_MAX"]):
        def v(name):
            x = params.get(name, {}).get(key)
            return None if x is None or x <= FILL + 1 else x
        rows.append({"date": dt.datetime.strptime(key, "%Y%m%d").date(), "tmax": v("T2M_MAX"),
                     "tmin": v("T2M_MIN"), "precip": v("PRECTOTCORR"), "gwet": v("GWETROOT")})
    return rows
