import json
from functools import lru_cache

from app.config import ROOT


@lru_cache
def _load():
    crops = json.loads((ROOT / "seeds" / "crops.json").read_text())
    rotations = json.loads((ROOT / "seeds" / "rotations.json").read_text())["rotations"]
    return ({c["id"]: c for c in crops["crops"]}, {c["id"]: c for c in crops["covers"]}, rotations)


def crops() -> dict[str, dict]:
    return _load()[0]


def covers() -> dict[str, dict]:
    return _load()[1]


def rotations() -> list[dict]:
    return _load()[2]


def crop_from_cdl(category: str | None) -> str | None:
    if not category:
        return None
    for cid, c in crops().items():
        if category in c["cdl"]:
            return cid
    return None


CROP_WORDS = {
    # English
    "corn": "corn", "maize": "corn", "soy": "soybeans", "soybean": "soybeans", "soybeans": "soybeans",
    "wheat": "winter_wheat", "sorghum": "sorghum", "milo": "sorghum", "sunflower": "sunflower",
    "sunflowers": "sunflower", "cotton": "cotton", "alfalfa": "alfalfa", "hay": "alfalfa", "peas": "field_peas",
    "pea": "field_peas", "chickpeas": "field_peas", "lentils": "field_peas", "oats": "oats", "canola": "canola",
    "rapeseed": "canola", "millet": "proso_millet", "triticale": "triticale", "rye": "triticale", "fallow": "fallow",
    "barley": "barley", "beans": "dry_beans", "bean": "dry_beans", "peanut": "peanuts", "peanuts": "peanuts",
    "potato": "potatoes", "potatoes": "potatoes", "tomato": "tomatoes", "tomatoes": "tomatoes",
    "beet": "sugarbeets", "beets": "sugarbeets", "sugarbeet": "sugarbeets", "sugarbeets": "sugarbeets",
    "rice": "rice", "sugarcane": "sugarcane", "cane": "sugarcane", "vegetables": "vegetables", "veggies": "vegetables",
    "onions": "vegetables", "peppers": "vegetables", "lettuce": "vegetables", "melons": "vegetables",
    # Spanish
    "maiz": "corn", "maíz": "corn", "soja": "soybeans", "soya": "soybeans", "trigo": "winter_wheat",
    "sorgo": "sorghum", "girasol": "sunflower", "algodon": "cotton", "algodón": "cotton", "heno": "alfalfa",
    "guisantes": "field_peas", "arvejas": "field_peas", "chicharos": "field_peas", "chícharos": "field_peas",
    "garbanzos": "field_peas", "lentejas": "field_peas", "avena": "oats", "colza": "canola", "mijo": "proso_millet",
    "centeno": "triticale", "barbecho": "fallow", "descanso": "fallow", "cebada": "barley", "frijol": "dry_beans",
    "frijoles": "dry_beans", "judias": "dry_beans", "judías": "dry_beans", "alubias": "dry_beans",
    "cacahuete": "peanuts", "cacahuetes": "peanuts", "mani": "peanuts", "maní": "peanuts", "papa": "potatoes",
    "papas": "potatoes", "patata": "potatoes", "patatas": "potatoes", "tomate": "tomatoes", "tomates": "tomatoes",
    "remolacha": "sugarbeets", "arroz": "rice", "caña": "sugarcane", "hortalizas": "vegetables",
    "verduras": "vegetables", "cebollas": "vegetables", "pimientos": "vegetables", "lechuga": "vegetables",
    # Basque
    "artoa": "corn", "arto": "corn", "garia": "winter_wheat", "gari": "winter_wheat", "garagarra": "barley",
    "garagar": "barley", "oloa": "oats", "olo": "oats", "soja": "soybeans", "ekilorea": "sunflower",
    "patata": "potatoes", "patatak": "potatoes", "tomatea": "tomatoes", "tomateak": "tomatoes", "babarrunak": "dry_beans",
    "ilarrak": "field_peas", "alpapa": "alfalfa", "errapea": "canola", "zekalea": "triticale", "lugorria": "fallow",
}


def crops_in_text(text: str) -> list[str]:
    """Crop ids mentioned in free text, in the order they appear."""
    import re
    out = []
    for w in re.findall(r"[a-záéíóúñü]+", (text or "").lower()):
        cid = CROP_WORDS.get(w)
        if cid and (not out or out[-1] != cid or len(out) < 2):
            out.append(cid)
    return out
