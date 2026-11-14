"""7-day forecast (Open-Meteo, open data, no key) for irrigation and frost alerts."""
from app.data.http import get_json

URL = "https://api.open-meteo.com/v1/forecast"


def forecast(lat: float, lon: float, days: int = 7) -> list[dict]:
    d = get_json(URL, {"latitude": lat, "longitude": lon, "forecast_days": days, "timezone": "auto",
                       "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,et0_fao_evapotranspiration"},
                 ttl_s=3 * 3600)["daily"]
    return [{"date": d["time"][i], "tmax": d["temperature_2m_max"][i], "tmin": d["temperature_2m_min"][i],
             "precip": d["precipitation_sum"][i], "et0": d["et0_fao_evapotranspiration"][i]}
            for i in range(len(d["time"]))]


def elevation(lat: float, lon: float) -> float | None:
    try:
        return get_json("https://api.open-meteo.com/v1/elevation", {"latitude": lat, "longitude": lon},
                        ttl_s=0)["elevation"][0]
    except Exception:
        return None
