"""NASA POWER (power.larc.nasa.gov): ~20 years of daily agro-climate at a point, worldwide.

Parameters: T2M_MAX/T2M_MIN (MERRA-2), PRECTOTCORR (bias-corrected precipitation, IMERG-informed),
GWETROOT (root-zone soil wetness, 0-1, from NASA GMAO MERRA-2 land model).
"""
import datetime as dt

from app.data.http import get_json

URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
PARAMS = "T2M_MAX,T2M_MIN,PRECTOTCORR,GWETROOT"
FILL = -999.0


def fetch_daily(lat: float, lon: float, years: int = 20) -> dict[str, dict[str, float]]:
    end = dt.date.today() - dt.timedelta(days=7)  # POWER has a few days latency
    start = dt.date(end.year - years, 1, 1)
    data = get_json(URL, {
        "parameters": PARAMS, "community": "AG", "latitude": round(lat, 3), "longitude": round(lon, 3),
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
