"""Is this pin on a farm field? Checked on the data the field analysis already fetched (no extra requests), so a
pin in the ocean, a town or a forest is re-asked instead of getting advice.

U.S.: the latest USDA Cropland Data Layer class at the pixel (30 m). Everywhere: the elevation model reads exactly
0.0 m over open sea (real low farmland reads negative, e.g. Imperial Valley -42 m).
"""

NOT_FIELD = {
    "Open Water": "open water",
    "Perennial Ice/Snow": "ice or snow",
    "Barren": "bare ground (rock, sand or a quarry)",
    "Woody Wetlands": "a wetland",
    "Herbaceous Wetlands": "a wetland",
    "Deciduous Forest": "a forest",
    "Evergreen Forest": "a forest",
    "Mixed Forest": "a forest",
}


def problem(features: dict) -> str | None:
    """What the pin is on, if it is clearly not a field ("open water", "a town or built-up area", ...); else None."""
    seq = [s for s in (features.get("rotation") or {}).get("sequence") or [] if s.get("category")]
    if seq:
        category = max(seq, key=lambda s: s["year"])["category"]
        if category.startswith("Developed"):
            return "a town or built-up area"
        return NOT_FIELD.get(category)
    if features.get("elevation_m") == 0.0:
        return "open water"
    return None
