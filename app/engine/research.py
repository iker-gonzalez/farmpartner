"""Published evidence behind FarmPartner's advice.

- Mudare et al. 2025, "Crop rotations synergize yield, nutrition, and revenue: a meta-analysis", Nature
  Communications 16:9552 (3,663 paired field-trial observations, 1980–2024): yield gain of the crop that follows.
- FAOSTAT Crops and livestock products (FAO, CC BY-NC-SA 3.0 IGO): what each country actually grows (2020–24).
- DRIVES database (Diverse Rotations Improve Valuable Ecosystem Services; drives-network.org): long-term rotation
  experiments in the U.S., Canada and Mexico. Country lookup: GeoNames (CC BY 4.0).
"""
import csv
import json
import math
from functools import lru_cache

from app.config import ROOT
from app.engine.knowledge import crops

MUDARE = "Mudare et al. 2025, Nature Communications (meta-analysis of 3,663 field trials)"
FAO = "FAOSTAT 2020–24"
DRIVES = "DRIVES long-term rotation experiments"
US_M49 = 840


# --- 1. Rotation effect on the next crop (meta-analysis) -----------------------------------------------------------
def _gain(prev: dict, nxt: dict) -> float | None:
    """% yield gain of nxt when it follows prev instead of itself (None when the paper says nothing about the pair)."""
    if prev["id"] == "fallow" or nxt["id"] == "fallow":
        return None
    if prev["id"] == nxt["id"]:
        return 0.0  # monoculture: the baseline
    cereal = nxt["family"] == "grass"
    if prev["id"] == "alfalfa":
        return 26.0 if cereal else 23.0           # fodder legume before a cereal: +26 %
    if prev["n_fixing"]:
        return 20.0 if cereal else 23.0           # legume pre-crop: +23 % overall, +20 % for cereals
    if {prev["id"], nxt["id"]} == {"corn", "cotton"}:
        return 20.0                               # "cotton before maize or maize before cotton: high gain"
    return 15.0 if cereal else 16.0               # non-legume break crop: +16 % (+15 % for cereals)


def rotation_effect(years: list[str], cover_map: dict | None = None) -> dict:
    """Average expected yield gain of each crop from the crop before it, plus whether a legume feeds a cereal."""
    C = crops()
    cover_map = cover_map or {}
    seq = [C[c] for c in years]
    n = len(seq)
    gains, legume_to_cereal = [], False
    for i in range(n):
        prev, nxt = seq[i - 1], seq[i]
        harvested_dbl = cover_map.get(str((i - 1) % n))
        if harvested_dbl == "dbl_soybeans":  # a double-crop legume is the real pre-crop of the next main crop
            prev = C["soybeans"]
        g = _gain(prev, nxt)
        if g is not None:
            gains.append(g)
            legume_to_cereal |= bool(prev["n_fixing"] and nxt["id"] in ("corn", "winter_wheat", "barley", "sorghum"))
    return {"yield_gain_pct": round(sum(gains) / len(gains), 1) if gains else 0.0, "legume_to_cereal": legume_to_cereal}


def effect_lines(eff: dict, is_current: bool) -> list[str]:
    out = []
    if eff["yield_gain_pct"] >= 5:
        out.append(f"📚 Field trials worldwide: crops in this rotation yield ~{eff['yield_gain_pct']:.0f}% more than "
                   f"in monoculture ({MUDARE}).")
    elif is_current and eff["yield_gain_pct"] == 0:
        out.append(f"📚 Monoculture forgoes the ~20% yield gain rotations give the next crop ({MUDARE}).")
    if eff["legume_to_cereal"]:
        out.append("📚 Rotating corn or wheat with legumes cut nitrogen fertilizer by 41–46% at the same yields "
                   f"in those trials ({MUDARE}).")
    return out


# --- 2. Country crops (FAOSTAT) --------------------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _points() -> list[tuple[float, float, int]]:
    with (ROOT / "data" / "country_points.csv").open() as fh:
        return [(float(r["lat"]), float(r["lon"]), int(r["m49"])) for r in csv.DictReader(fh)]


@lru_cache(maxsize=1)
def _fao() -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    with (ROOT / "data" / "fao_country_crops.csv").open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out.setdefault(int(r["m49"]), []).append(
                {"country": r["country"], "item": r["item"], "crop_id": r["crop_id"] or None,
                 "area_ha": float(r["area_ha"]), "yield_t_ha": float(r["yield_t_ha"]) if r["yield_t_ha"] else None})
    for v in out.values():
        v.sort(key=lambda r: -r["area_ha"])
    return out


@lru_cache(maxsize=2048)
def country_of(lat: float, lon: float) -> int | None:
    """UN M49 code of the country of the nearest town (≥15k people) within ~300 km."""
    best, best_d = None, 1e9
    k = math.cos(math.radians(lat))
    for la, lo, m49 in _points():
        d = (la - lat) ** 2 + ((lo - lon) * k) ** 2
        if d < best_d:
            best, best_d = m49, d
    return best if best_d < 2.7 ** 2 else None


def country_crops(lat: float, lon: float, top: int = 10) -> dict | None:
    m49 = country_of(round(lat, 2), round(lon, 2))
    rows = _fao().get(m49) if m49 else None
    if not rows:
        return None
    modeled: dict[str, float] = {}
    for r in rows:
        if r["crop_id"]:
            modeled[r["crop_id"]] = modeled.get(r["crop_id"], 0) + r["area_ha"]
    total = sum(modeled.values()) or 1
    return {"m49": m49, "country": rows[0]["country"],
            "top": [{"item": r["item"], "area_ha": round(r["area_ha"]), "yield_t_ha": r["yield_t_ha"],
                     "crop_id": r["crop_id"]} for r in rows[:top]],
            "grown_crop_ids": sorted(c for c, a in modeled.items() if a / total >= 0.005), "source": FAO}


def crop_pool(country: dict | None) -> set[str] | None:
    """Crops worth searching in this country (None = no restriction, e.g. the U.S. where CDL guides us)."""
    if not country or country["m49"] == US_M49:
        return None
    return set(country["grown_crop_ids"]) | {"fallow"}


# --- 3. Long-term rotation experiments nearby (DRIVES) ---------------------------------------------------------------
@lru_cache(maxsize=1)
def _sites() -> list[dict]:
    return json.loads((ROOT / "data" / "drives_sites.json").read_text())


def _km(a_lat, a_lon, b_lat, b_lon) -> float:
    p = math.pi / 180
    h = (math.sin((b_lat - a_lat) * p / 2) ** 2
         + math.cos(a_lat * p) * math.cos(b_lat * p) * math.sin((b_lon - a_lon) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(h))


def nearby_trials(lat: float, lon: float, rain_mm: float | None, max_km: float = 1000, n: int = 2) -> list[dict]:
    out = []
    for s in _sites():
        if not s["rotations"]:
            continue
        d = _km(lat, lon, s["lat"], s["lon"])
        if d > max_km:
            continue
        if rain_mm and s.get("map_mm") and not (0.6 <= s["map_mm"] / rain_mm <= 1.6):
            continue  # a very different rainfall regime is not "near" in the sense that matters
        out.append({"name": s["name"], "place": f"{s['city']}, {s['state']}", "since": s["start_year"],
                    "km": round(d), "rotations": [r["years"] for r in s["rotations"]], "source": DRIVES})
    return sorted(out, key=lambda t: t["km"])[:n]


def tested_at(years: list[str], trials: list[dict]) -> dict | None:
    """A nearby long-term trial that runs this same set of main crops."""
    want = set(years) - {"fallow"}
    for t in trials:
        if any(set(r) - {"fallow"} == want for r in t["rotations"]):
            return t
    return None
