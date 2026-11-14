"""Transparent, rule-based rotation scoring.

Every sub-score is 0-100 and is computed from a handful of named features, so each number can be traced back to
the data source in the explanation. The LLM never invents these numbers; it only rephrases them.
"""
from app.engine.knowledge import covers, crops, rotations

SUBSCORES = ["water", "soil", "erosion", "pest", "income", "resilience", "forage"]
BASE_WEIGHTS = {"water": 1.0, "soil": 1.0, "erosion": 1.0, "pest": 1.0, "income": 1.0, "resilience": 1.0, "forage": 0.3}
PRIORITY_BOOST = {
    "save_water": {"water": 4.0, "resilience": 1.5},
    "soil_health": {"soil": 4.0, "erosion": 1.5},
    "income": {"income": 4.0, "resilience": 1.5},
    "less_erosion": {"erosion": 4.0, "soil": 1.5},
    "forage": {"forage": 4.0},
}
PRIORITY_LABELS = {"save_water": "saving water", "soil_health": "soil health", "income": "income",
                   "less_erosion": "less erosion", "forage": "livestock forage"}


def clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def covered_months(years: list[str], cover_map: dict) -> list[set[int]]:
    """For each rotation year, the set of months (1-12) with a living crop or cover in the field."""
    n = len(years)
    cov = [set() for _ in range(n)]
    for i, cid in enumerate(years):
        c = crops()[cid]
        if c["season"] == "perennial":
            cov[i] |= set(range(1, 13))
        elif c["season"] == "cool" and c["plant_month"] > c["harvest_month"]:  # winter crop: planted the fall before
            cov[(i - 1) % n] |= set(range(c["plant_month"], 13))
            cov[i] |= set(range(1, c["harvest_month"] + 1))
        elif c["plant_month"]:
            cov[i] |= set(range(c["plant_month"], c["harvest_month"] + 1))
    for i_str, cover_id in cover_map.items():
        i = int(i_str)
        harvest = crops()[years[i]]["harvest_month"] or 7
        for m in covers()[cover_id]["months"]:
            # months after this year's harvest belong to this year, early months to the next year
            cov[i if m > harvest else (i + 1) % n].add(m)
    return cov


def erosion_risk_index(f: dict) -> tuple[float, list[str]]:
    reasons, risk = [], 1.0
    heavy = (f.get("climate") or {}).get("heavy_rain_days_per_yr") or 0
    soil = f.get("soil") or {}
    if heavy >= 4:
        risk += 0.7; reasons.append(f"{heavy:g} heavy-rain days (>1 in) per year")
    elif heavy >= 2:
        risk += 0.3
    if (soil.get("slope_pct") or 0) >= 4:
        risk += 0.7; reasons.append(f"{soil['slope_pct']:g}% slope")
    if (soil.get("k_factor") or 0) >= 0.37:
        risk += 0.4; reasons.append(f"erodible soil (K={soil['k_factor']})")
    if (soil.get("sand_pct") or 0) >= 60:
        risk += 0.3; reasons.append("sandy soil prone to wind erosion")
    return risk, reasons


def score_rotation(rot: dict, f: dict, irrigated: bool | None) -> dict:
    C = crops()
    years, cover_map = rot["years"], rot.get("covers", {})
    clim = f.get("climate") or {}
    rain = clim.get("annual_precip_mm") or 600
    heat = clim.get("heat_days_recent5") or clim.get("heat_days_per_yr") or 0
    gdd = clim.get("season_gdd_c") or 2500
    main = [C[c] for c in years]
    n = len(years)

    # Feasibility (hard filters)
    issues = []
    for c in main:
        if c["gdd_c"] and c["gdd_c"] > gdd * 0.95:
            issues.append(f"{c['name']} needs ~{c['gdd_c']} growing degree days; this site gets ~{gdd}")
    if not irrigated and rain < rot.get("min_rain_mm", 0):
        issues.append(f"needs ~{rot['min_rain_mm']} mm rain/yr without irrigation; this site averages {rain:.0f} mm")

    # Water: seasonal crop water need vs effective rainfall
    cover_water = sum(covers()[cv]["water_use_mm"] for cv in cover_map.values()) / n
    need = sum(c["water_need_mm"] for c in main) / n + cover_water
    eff = rain * 0.7
    ratio = need / eff if eff else 2
    drought = sum(c["drought_tolerance"] for c in main) / n
    water = clamp(110 - 55 * ratio + (drought - 3) * 6)
    if irrigated:  # irrigation removes the yield risk but not the aquifer cost: score stays need-based
        water = clamp(100 - need / 9)

    # Soil health
    families = {c["family"] for c in main if c["family"] != "none"} | {covers()[cv]["family"] for cv in cover_map.values()}
    fallow_share = sum(1 for c in main if c["id"] == "fallow") / n
    soil = clamp(
        sum(c["residue"] for c in main) / n * 10
        + sum(1 for c in main if c["n_fixing"]) / n * 20
        + sum(1 for cv in cover_map.values() if covers()[cv]["n_fixing"]) / n * 8
        + len(cover_map) / n * 18
        + (len(families) - 1) * 8
        + sum(1 for c in main if c["root"] == "deep") / n * 10
        - fallow_share * 20 + 10
    )

    # Erosion: bare months per year, weighted by site erosion risk
    cov = covered_months(years, cover_map)
    bare_per_year = sum(12 - len(m) for m in cov) / n
    risk, risk_reasons = erosion_risk_index(f)
    erosion = clamp(100 - bare_per_year * (4 + 4 * risk))

    # Pest / disease cycle break
    pest = 85.0
    for i in range(n):
        a, b = main[i], main[(i + 1) % n]
        if a["id"] == b["id"] and a["id"] != "fallow":
            pest -= 40 / (n / 2)
        elif a["family"] == b["family"] and a["family"] != "none":
            pest -= 12
    pest = clamp(pest + (len(families) - 1) * 5)

    # Resilience to heat & drought at this site
    res_vals = []
    for c in main:
        if c["id"] == "fallow":
            continue
        r = 60 + (c["drought_tolerance"] - 3) * 10 * (1.5 if rain < 600 and not irrigated else 0.7)
        if c["season"] == "warm":
            r += (c["heat_tolerance"] - 3) * (12 if heat >= 20 else 5)
        res_vals.append(r)
    resilience = clamp(sum(res_vals) / len(res_vals) if res_vals else 50)

    # Income proxy (relative only; no prices) discounted by climate risk
    income = clamp(sum(c["income"] for c in main) / n * 20 * (0.75 + resilience / 400))
    forage = clamp((sum(c["forage"] for c in main) / n + (1.5 if rot.get("extra_forage") else 0)) * 20)

    return {
        "id": rot["id"], "name": rot["name"], "years": years, "covers": cover_map,
        "feasible": not issues, "issues": issues,
        "scores": {k: round(v) for k, v in {"water": water, "soil": soil, "erosion": erosion, "pest": pest,
                                             "income": income, "resilience": resilience, "forage": forage}.items()},
        "metrics": {"water_need_mm_per_yr": round(need), "effective_rain_mm": round(eff),
                    "bare_months_per_yr": round(bare_per_year, 1), "fallow_share": round(fallow_share, 2),
                    "families": sorted(families), "erosion_risk": round(risk, 1), "erosion_reasons": risk_reasons},
    }


def weights_for(priorities: list[str]) -> dict:
    w = dict(BASE_WEIGHTS)
    for p in priorities:
        for k, v in PRIORITY_BOOST.get(p, {}).items():
            w[k] = max(w[k], v)
    return w


def total(scores: dict, weights: dict) -> float:
    return round(sum(scores[k] * weights[k] for k in SUBSCORES) / sum(weights.values()), 1)


def _same_cycle(a: list[str], b: list[str]) -> bool:
    if len(a) != len(b):
        return False
    return any(a[i:] + a[:i] == b for i in range(len(a)))


def canonical_cycle(pattern: list[str]) -> list[str]:
    """Rotate a detected cycle into the order of the matching template (e.g. wheat first), if there is one."""
    match = next((r for r in rotations() if _same_cycle(r["years"], pattern)), None)
    return list(match["years"]) if match else list(pattern)


def compare(f: dict, priorities: list[str], irrigated: bool | None, current: list[str] | None = None,
            n_alternatives: int = 2) -> dict:
    """Score the farmer's current rotation plus the best alternatives. Returns 3 strategies by default."""
    weights = weights_for(priorities)
    current = current or (f.get("rotation") or {}).get("pattern") or []
    out = []
    cur = None
    if current:
        match = next((r for r in rotations() if _same_cycle(r["years"], current) and not r.get("covers")), None)
        if match:
            current = list(match["years"])  # show the cycle in its conventional order (e.g. wheat first)
        label = (f"Continuous {crops()[current[0]]['name'].lower()}" if len(set(current)) == 1
                 else " → ".join(crops()[c]["name"] for c in current))
        cur_rot = {"id": "current", "name": "Your current rotation: " + label,
                   "years": current, "covers": {}, "min_rain_mm": match["min_rain_mm"] if match else 0}
        cur = score_rotation(cur_rot, f, irrigated)
        cur["is_current"] = True
        cur["total"] = total(cur["scores"], weights)
    cands = []
    for r in rotations():
        if current and _same_cycle(r["years"], current) and not r.get("covers"):
            continue
        if r.get("requires_any") and not set(r["requires_any"]) & set(current or []):
            continue  # specialty rotations (vegetables, beets, rice...) only for farmers who already grow those crops
        s = score_rotation(r, f, irrigated)
        if not s["feasible"]:
            continue
        s["is_current"] = False
        s["total"] = total(s["scores"], weights)
        cands.append(s)
    cands.sort(key=lambda s: s["total"], reverse=True)
    specialty_ids = {r["id"] for r in rotations() if r.get("requires_any")}
    specialty = [s for s in cands if s["id"] in specialty_ids]
    if len(specialty) >= 2:  # a vegetable/beet/rice grower gets options that keep their crop
        cands = specialty
    # Two alternatives with different jobs: the best fit for the farmer's priorities, and the best soil builder
    # (the challenge goal) that still fits this climate.
    picked = []
    if cands:
        best = cands[0]
        best["role"] = "Best match for your priorities"
        picked.append(best)
        base_soil = (cur["scores"]["soil"] + cur["scores"]["erosion"]) / 2 if cur else 0
        builders = [s for s in cands[1:] if (s["scores"]["soil"] + s["scores"]["erosion"]) / 2 >= base_soil + 10
                    and s["total"] >= best["total"] - 15]
        builders.sort(key=lambda s: (s["scores"]["soil"] + s["scores"]["erosion"]) / 2 + s["total"] / 2, reverse=True)
        second = builders[0] if builders else (cands[1] if len(cands) > 1 else None)
        if second:
            second["role"] = "Best soil builder" if builders else "Runner-up"
            picked.append(second)
    if cur:
        cur["role"] = "What you do today"
    strategies = ([cur] if cur else []) + picked[:n_alternatives]
    return {"priorities": priorities, "weights": weights, "irrigated": irrigated, "strategies": strategies}
