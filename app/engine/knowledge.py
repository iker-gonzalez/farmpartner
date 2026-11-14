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
    "corn": "corn", "maize": "corn", "soy": "soybeans", "soybean": "soybeans", "soybeans": "soybeans",
    "wheat": "winter_wheat", "sorghum": "sorghum", "milo": "sorghum", "sunflower": "sunflower",
    "sunflowers": "sunflower", "cotton": "cotton", "alfalfa": "alfalfa", "hay": "alfalfa", "peas": "field_peas",
    "pea": "field_peas", "oats": "oats", "canola": "canola", "millet": "proso_millet", "triticale": "triticale",
    "fallow": "fallow",
}


def crops_in_text(text: str) -> list[str]:
    """Crop ids mentioned in free text, in the order they appear."""
    import re
    out = []
    for w in re.findall(r"[a-z]+", (text or "").lower()):
        cid = CROP_WORDS.get(w)
        if cid and (not out or out[-1] != cid or len(out) < 2):
            out.append(cid)
    return out
