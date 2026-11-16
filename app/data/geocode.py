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


# Farmer's language → countries whose postal codes to try (Zippopotam covers these), most likely first.
LANG_COUNTRIES = {"en": ["us"], "es": ["es", "mx", "ar"], "eu": ["es"], "ca": ["es"], "gl": ["es"], "fr": ["fr", "be", "ch"],
                  "de": ["de", "at", "ch"], "it": ["it"], "tr": ["tr"], "hi": ["in"], "bn": ["in", "bd"], "ru": ["ru"],
                  "nl": ["nl", "be"], "pl": ["pl"], "pt": ["br", "pt"], "ja": ["jp"]}
# Digits-only postal code lengths per country (the rest use 5, or 4 or 5).
CODE_LEN = {"us": (5,), "es": (5,), "mx": (5,), "fr": (5,), "de": (5,), "it": (5,), "tr": (5,), "in": (6,), "ru": (6,),
            "ar": (4,), "be": (4,), "ch": (4,), "at": (4,), "bd": (4,), "au": (4,), "nz": (4,), "za": (4,), "dk": (4,),
            "no": (4,), "hu": (4,), "nl": (4,), "ph": (4,)}


POSTAL_WORDS = re.compile(r"\b(zip( ?code)?|post(al)? ?code|postcode|c[oó]digo postal|cp|c\.p\.|code postal|plz|postleitzahl|"
                          r"cap|posta kodu?|my|mi|is|es|el|the|est|ist)\b", re.I)


def postcode_countries(phone: str | None, lang: str | None = None, country: str | None = None) -> list[str]:
    """Where a typed postal code may be, most likely first: the phone's country (WhatsApp/SMS), the browser's
    region (web chat), the countries of the farmer's language, then the U.S."""
    out = [phone_country(phone)] if "+" in (phone or "") else []
    out += [(country or "").lower()] if country else []
    out += LANG_COUNTRIES.get(lang or "", [])
    return list(dict.fromkeys(c for c in out + ["us"] if re.fullmatch(r"[a-z]{2}", c)))


def zip_to_point(text: str, country: str | list[str] = "us") -> tuple[float, float, str] | None:
    """A postal code in the message → (lat, lon, label). Tries each country in turn; the first that knows it wins."""
    text = text or ""
    # "01409", "vivo en 01409", "my zip is 67846" — but not "tengo 15000 olivos": a number among other words is not a code.
    rest = FILLER.sub(" ", POSTAL_WORDS.sub(" ", re.sub(r"\d+", " ", text)))
    if re.search(r"[^\W_]", rest) and not POSTAL_WORDS.search(text):
        return None
    for cc in [country] if isinstance(country, str) else country:
        m = next((m for n in CODE_LEN.get(cc, (5, 4)) for m in [re.search(rf"(?<!\d)(\d{{{n}}})(?!\d)", text or "")] if m), None)
        if not m:
            continue
        try:
            d = get_json(f"https://api.zippopotam.us/{cc}/{m.group(1)}", ttl_s=0)
            p = d["places"][0]
        except Exception:
            continue
        label = f"{p['place name']}, {p['state abbreviation'] or p['state']}"
        return float(p["latitude"]), float(p["longitude"]), label if cc == "us" else f"{label}, {cc.upper()}"
    return None


STOPWORDS = {"i", "im", "live", "living", "my", "our", "we", "farm", "field", "is", "am", "are", "the", "a", "at",
             "near", "close", "to", "from", "mi", "mis", "campo", "finca", "parcela", "vivo", "estoy", "soy", "es",
             "el", "la", "los", "las", "de", "del", "cerca", "por", "aqui", "here", "there", "nire", "naiz", "ma",
             "mon", "je", "suis", "habite", "près", "pres", "minha", "meu", "fazenda", "moro", "ich", "wohne", "bei",
             "hello", "hi", "hey", "hola", "buenas", "kaixo", "ola", "bonjour", "ciao", "hallo", "thanks", "thank",
             "you", "gracias", "ok", "okay", "yes", "no", "si", "sí", "bai", "ez", "please", "porfa", "vale", "good",
             "morning", "afternoon", "dias", "días", "tardes", "what", "how", "que", "qué", "como", "cómo",
             "who", "where", "which", "why", "quien", "quién", "donde", "dónde", "cual", "cuál", "sabe", "se", "sé"}


def _plain(x: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", (x or "").lower()) if not unicodedata.combining(c))


def _close(query: str, name: str) -> bool:
    """Every searched word matches a word of the place name, allowing one letter's difference
    ("Galapago" ≈ "Galápagos")."""
    def near(a: str, b: str) -> bool:
        return a == b or (abs(len(a) - len(b)) <= 1 and (a.startswith(b) or b.startswith(a)))
    name_words = re.findall(r"\w+", _plain(name))
    return all(any(near(q, n) for n in name_words) for q in re.findall(r"\w+", _plain(query)) if len(q) > 2)


def place_to_point(text: str, lang: str = "en") -> tuple[float, float, str] | None:
    """A town, region or island name ("Bilbao", "soy residente en Bilbao", "Islas Galápagos", "Garden City Kansas")
    via Open-Meteo's free geocoder. Tries the whole phrase, then shorter runs of words; a partial match must contain
    the words searched, and leftover words (a state or country) pick between same-named places."""
    cleaned = FILLER.sub(" ", re.sub(r"[\d,.;:!?¿¡\"'()]", " ", text or "")).strip()
    words = [w for w in cleaned.split() if _plain(w) not in STOPWORDS]
    if not words or len(words) > 6:
        return None
    runs = [" ".join(words[i:i + n]) for n in range(len(words), 0, -1) for i in range(len(words) - n + 1)]
    langs = list(dict.fromkeys([lang or "en", "en", "es"]))
    for run in runs:
        if len(run) < 4:
            continue
        strict = run != " ".join(words)
        others = [_plain(w) for w in words if _plain(w) not in _plain(run)]
        for lg in langs if not strict else langs[:1]:
            try:
                res = get_json("https://geocoding-api.open-meteo.com/v1/search",
                               {"name": run, "count": 10, "language": lg}, ttl_s=0).get("results") or []
            except Exception:
                continue
            res = [r for r in res if _close(run, r.get("name", ""))]  # "hello" must not land on "Hellouw"
            if strict:
                if others:  # "Garden City Kansas": keep the Garden City whose state/country matches "Kansas"
                    res = [r for r in res if any(o in _plain(" ".join(str(r.get(k, "")) for k in
                                                ("admin1", "admin2", "country", "country_code"))) for o in others)] or (
                        res if len(run.split()) > 1 else [])
            if res:
                r = res[0] if strict else max(res[:5], key=lambda r: r.get("population") or 0)
                label = ", ".join(x for x in (r.get("name"), r.get("admin1"), r.get("country_code")) if x)
                # the API omits zero values: the Galápagos (on the equator) come back without "latitude"
                return float(r.get("latitude", 0.0)), float(r.get("longitude", 0.0)), label
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
