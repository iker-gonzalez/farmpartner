"""Text locations → lat/lon: postal codes (in the farmer's phone country) and place names, for farmers who don't
send a pin. Both are approximate (a town centre), so they skip the "is this a field" check."""
import re

from app.data.http import get_json

# Phone calling code → country for postal-code lookups (Zippopotam). U.S. is the default.
CALLING = {"1": "us", "34": "es", "52": "mx", "33": "fr", "49": "de", "39": "it", "44": "gb", "351": "pt", "55": "br",
           "54": "ar", "81": "jp", "91": "in", "61": "au", "57": "co", "48": "pl", "31": "nl", "32": "be", "41": "ch",
           "43": "at", "45": "dk", "46": "se", "47": "no", "358": "fi", "90": "tr", "63": "ph", "64": "nz",
           "66": "th", "27": "za", "420": "cz", "421": "sk", "36": "hu", "40": "ro", "385": "hr", "56": "cl"}
FILLER = re.compile(r"\b(i live in|i'm in|i am in|my farm is in|near|in|soy residente en|vivo en|estoy en|mi campo "
                    r"est[aá] en|cerca de|en|bizi naiz|naiz|je suis à|j'habite à|moro em|estou em|ich wohne in)\b", re.I)


def phone_country(phone: str | None) -> str:
    digits = re.sub(r"\D", "", (phone or "").split(":")[-1]) if "+" in (phone or "") else ""
    for n in (3, 2, 1):
        if digits[:n] in CALLING:
            return CALLING[digits[:n]]
    return "us"


def zip_to_point(text: str, country: str = "us") -> tuple[float, float, str] | None:
    m = re.search(r"\b(\d{5})\b" if country == "us" else r"\b(\d{4,5})\b", text or "")
    if not m:
        return None
    try:
        d = get_json(f"https://api.zippopotam.us/{country}/{m.group(1)}", ttl_s=0)
    except Exception:
        return None
    p = d["places"][0]
    return float(p["latitude"]), float(p["longitude"]), f"{p['place name']}, {p['state abbreviation'] or p['state']}"


def place_to_point(text: str, lang: str = "en") -> tuple[float, float, str] | None:
    """A town or region name ("Bilbao", "soy residente en Bilbao") via Open-Meteo's free geocoder."""
    cleaned = FILLER.sub(" ", re.sub(r"[\d,.;:!?¿¡\"'()]", " ", text or "")).strip()
    words = cleaned.split()
    if not words or len(words) > 6:
        return None
    tries = [" ".join(words)] + [w for w in words if w[:1].isupper() and len(w) > 2] + [words[-1]]
    for name in dict.fromkeys(tries):
        try:
            res = get_json("https://geocoding-api.open-meteo.com/v1/search",
                           {"name": name, "count": 1, "language": lang or "en"}, ttl_s=0).get("results") or []
        except Exception:
            continue
        if res:
            r = res[0]
            label = ", ".join(x for x in (r.get("name"), r.get("admin1"), r.get("country_code")) if x)
            return float(r["latitude"]), float(r["longitude"]), label
    return None

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
