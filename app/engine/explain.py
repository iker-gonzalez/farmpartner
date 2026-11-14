"""Deterministic explanations: why / evidence / benefits / gaps / risks for each strategy, plus chat formatting.

Farmers in the U.S. think in inches, °F and acres, so everything user-facing is converted here.
"""
from app.engine.knowledge import covers, crops
from app.engine.scoring import PRIORITY_LABELS

DISCLAIMER = "Suggestions from satellite & public data — please check with your local extension agent before changing plans."
SCORE_LABELS = {"water": "water use", "soil": "soil health", "erosion": "erosion protection", "pest": "pest & disease break",
                "income": "income potential", "resilience": "heat & drought resilience", "forage": "forage"}


def inch(mm):
    return None if mm is None else round(mm / 25.4, 1)


def fahr(c):
    return None if c is None else round(c * 9 / 5 + 32)


MONTH_NAMES = {"01": "January", "02": "February", "03": "March", "04": "April", "05": "May", "06": "June",
               "07": "July", "08": "August", "09": "September", "10": "October", "11": "November", "12": "December"}


def evidence(f: dict) -> list[dict]:
    c, soil, ndvi, g = f.get("climate") or {}, f.get("soil") or {}, f.get("ndvi") or {}, f.get("grace")
    ev = []
    if c.get("annual_precip_mm"):
        ev.append({"metric": "Average rainfall", "value": f'{inch(c["annual_precip_mm"])} in/yr ({c["years"]})',
                   "source": "NASA POWER"})
        ev.append({"metric": "Water balance (rain − crop water demand)", "value": f'{inch(c["water_balance_mm"])} in/yr',
                   "source": "NASA POWER (Hargreaves ET₀)"})
    if c.get("last_12mo_precip_pct_normal") is not None:
        ev.append({"metric": "Rain in the last 12 months", "value": f'{c["last_12mo_precip_pct_normal"]}% of normal',
                   "source": "NASA POWER"})
    if c.get("heat_days_recent5") is not None:
        ev.append({"metric": "Days above 95°F", "value": f'{c["heat_days_recent5"]}/yr recently vs {c["heat_days_early"]}/yr earlier',
                   "source": "NASA POWER"})
    if c.get("median_last_spring_frost"):
        ev.append({"metric": "Typical frost dates", "value": f'last {c["median_last_spring_frost"]}, first {c["median_first_fall_frost"]} '
                   f'({c["frost_free_days"]:.0f} frost-free days)', "source": "NASA POWER"})
    if c.get("heavy_rain_days_per_yr") is not None:
        ev.append({"metric": "Heavy-rain days (>1 in)", "value": f'{c["heavy_rain_days_per_yr"]}/yr', "source": "NASA POWER"})
    if c.get("soil_wetness_now") is not None and c.get("soil_wetness_normal"):
        pct = round(100 * (c["soil_wetness_now"] / c["soil_wetness_normal"] - 1))
        ev.append({"metric": "Root-zone soil moisture (last 60 days)", "value": f'{pct:+d}% vs normal',
                   "source": "NASA GMAO MERRA-2 GWETROOT via POWER"})
    sm = f.get("smap")
    if sm and sm.get("rootzone_percentile") is not None:
        pct = round(sm["rootzone_percentile"])
        word = "drier" if pct < 50 else "wetter"
        ev.append({"metric": f"Root-zone soil moisture (SMAP, 30 days to {sm['as_of']})",
                   "value": f"{pct}th percentile — {word} than {pct if pct >= 50 else 100 - pct}% of the record",
                   "source": "NASA SMAP L4 (9 km)"})
    elif sm and sm.get("rootzone_m3m3") is not None:
        # SMAP L4's percentile layer is often fill (-9999); report the measured water content and its trend.
        now_pct = round(sm["rootzone_m3m3"] * 100)
        monthly = sm.get("monthly_rootzone") or {}
        first = next(iter(monthly.items()), None)
        trend = ""
        if first and first[1] is not None and len(monthly) >= 3:
            then_pct = round(first[1] * 100)
            month = MONTH_NAMES.get(first[0][5:7], first[0])
            trend = (f", up from {then_pct}% in {month}" if now_pct > then_pct
                     else f", down from {then_pct}% in {month}" if now_pct < then_pct else f", same as {month}")
        ev.append({"metric": f"Root-zone soil moisture (SMAP, 30 days to {sm['as_of']})",
                   "value": f"{now_pct}% water by volume{trend}",
                   "source": "NASA SMAP L4 (9 km)"})
    eco = f.get("ecostress")
    if eco and eco.get("latest"):
        summer = f", summer avg {eco['summer_mean_esi']}" if eco.get("summer_mean_esi") is not None else ""
        ev.append({"metric": "Crop water stress (ECOSTRESS ESI: 1 = no stress, <0.4 = stressed)",
                   "value": f"latest {eco['latest']['esi']} ({eco['latest']['date']}){summer}; "
                            f"stressed in {round(eco['stressed_share'] * 100)}% of {eco['observations']} passes",
                   "source": "NASA ECOSTRESS (70 m, your field)"})
    if ndvi.get("bare_months"):
        ev.append({"metric": "Months the field looks bare from space", "value": ", ".join(ndvi["bare_months"]),
                   "source": "NASA MODIS NDVI"})
    if soil:
        ev.append({"metric": "Soil", "value": f'{soil["map_unit"]}; organic matter {soil["om_pct"]}%, slope {soil["slope_pct"]}%',
                   "source": "USDA NRCS SSURGO"})
    if g:
        ev.append({"metric": "Water storage trend (groundwater + soil + surface)",
                   "value": f'{g["trend_cm_per_yr"] / 2.54:+.2f} in/yr ({g["trend_cm_per_yr"]:+.1f} cm/yr), {g["period"]}',
                   "source": "NASA GRACE/GRACE-FO"})
    return ev


def _why(s: dict, cur: dict | None, priorities: list[str]) -> list[str]:
    if s.get("is_current"):
        return ["This is what satellite crop maps (USDA CDL) show on your field in recent years — the baseline to compare against."]
    out = []
    if cur:
        deltas = sorted(((s["scores"][k] - cur["scores"][k], k) for k in s["scores"]), reverse=True)
        for d, k in deltas[:3]:
            if d >= 8:
                out.append(f"Better {SCORE_LABELS[k]} than today (+{d} points).")
    if cur and s.get("overall") is not None and s["overall"] - cur.get("overall", 0) >= 3:
        out.append(f"Better overall too: {s['overall']:.0f}/100 vs {cur['overall']:.0f} today.")
    if priorities:
        out.append("Ranked high for your goal: " + ", ".join(PRIORITY_LABELS.get(p, p) for p in priorities) + ".")
    return out or ["Scores close to your current rotation on most measures."]


def _benefits(s: dict, f: dict) -> list[str]:
    C, CV = crops(), covers()
    main = [C[c] for c in s["years"]]
    b = []
    legumes = sorted({c["name"] for c in main if c["n_fixing"]})
    if legumes:
        b.append(f"Nitrogen-fixing {', '.join(legumes).lower()} can cut fertilizer needs for the next crop.")
    for cv in sorted(set(s["covers"].values())):
        b.append(f"{CV[cv]['name']}: {CV[cv]['note']}")
    deep = sorted({c["name"] for c in main if c["root"] == "deep"})
    if deep:
        b.append(f"Deep-rooted {', '.join(deep).lower()} reaches stored soil water and improves infiltration.")
    tough = sorted({c["name"] for c in main if c["drought_tolerance"] >= 4 and c["id"] != "fallow"})
    if tough:
        b.append(f"{', '.join(tough)}: handles heat and drought better than corn or soybeans.")
    if len(s["metrics"]["families"]) >= 3:
        b.append(f"{len(s['metrics']['families'])} plant families break pest and disease cycles.")
    b.append(f"Field is bare about {s['metrics']['bare_months_per_yr']} months a year in this plan.")
    return b


def _water_benefit(s: dict, cur: dict | None, f: dict) -> str | None:
    decline = _grace_decline(f)
    if not (decline and cur and not s.get("is_current")):
        return None
    saved = cur["metrics"]["water_need_mm_per_yr"] - s["metrics"]["water_need_mm_per_yr"]
    if saved >= 40:
        return (f"Needs ~{inch(saved)} in/yr less water than today, where NASA GRACE shows water storage "
                f"falling ~{decline} in/yr.")
    return None


def _grace_decline(f: dict) -> float | None:
    """Inches/yr of water-storage loss if NASA GRACE shows a meaningful decline here, else None."""
    g = f.get("grace")
    if g and g["trend_cm_per_yr"] <= -0.5:
        return round(-g["trend_cm_per_yr"] / 2.54, 2)
    return None


def _risks(s: dict, f: dict, irrigated: bool | None) -> list[str]:
    C = crops()
    ids = set(s["years"])
    r = list(s.get("issues", []))
    if s.get("generated"):
        r.insert(0, "A new combination found by FarmPartner's search, not a standard rotation: less field-tested, "
                    "so try it on part of a field first and check it with your extension agent.")
    dbl = [covers()[cv]["short"] for cv in set(s.get("covers", {}).values()) if covers()[cv].get("harvested")]
    if dbl:
        r.append(f"{', '.join(sorted(dbl)).capitalize()}: tight timing after the first harvest — plant fast, and "
                 "check herbicide carryover and local demand.")
    decline = _grace_decline(f)
    if decline and irrigated and s["metrics"]["water_need_mm_per_yr"] >= 450:
        r.append(f"NASA GRACE shows water storage here falling ~{decline} in/yr since 2002 — a thirsty irrigated "
                 "rotation adds to the aquifer drawdown.")
    niche = {"sorghum": "grain sorghum", "sunflower": "sunflower", "canola": "canola", "field_peas": "field peas",
             "proso_millet": "proso millet", "cotton": "cotton (needs a gin nearby)", "triticale": "forage triticale",
             "alfalfa": "hay"}
    m = [v for k, v in niche.items() if k in ids]
    if m:
        r.append(f"Check local buyers/elevators and prices for {', '.join(m)}.")
    if s["covers"] and not irrigated and ((f.get("climate") or {}).get("annual_precip_mm") or 999) < 600:
        r.append("In a dry year a cover crop can use water the next cash crop needs — terminate early if it is dry.")
    if "alfalfa" in ids:
        r.append("Alfalfa takes a year to establish and needs hay equipment or a buyer.")
    if {"canola", "field_peas", "sunflower"} & ids:
        r.append("May need different header/planter settings and herbicide plans (check carryover).")
    if s.get("is_current") and s["scores"]["pest"] < 40:
        r.append("Planting the same crop back-to-back builds up pests like corn rootworm.")
    if s.get("is_current") and s["metrics"]["fallow_share"] > 0:
        r.append("Fallow years leave soil bare and slowly burn organic matter.")
    return r


def gaps(f: dict) -> list[str]:
    g = ["No yield, price or field-level sensor data yet — reply with your own rain gauge and yields to improve this.",
         "Weather and soil moisture are ~50 km NASA model grids, not your exact field."]
    if not (f.get("rotation") or {}).get("is_cropland"):
        g.append("Could not read your crop history from satellite maps; tell me what you grew.")
    else:
        g.append("Crop history comes from 30 m satellite crop maps, which can mislabel some years.")
    if not f.get("ndvi"):
        g.append("Satellite vegetation (MODIS NDVI) was not available yet for this field.")
    a = f.get("appeears") or {}
    jobs = {} if "task_id" in a else a
    waiting = [n for k, n in (("smap", "soil moisture (SMAP)"), ("eco", "field-level crop stress (ECOSTRESS)"))
               if (jobs.get(k) or {}).get("status") in ("queued", "pending", "processing")]
    if waiting:
        g.append("Satellite " + " and ".join(waiting) + " still processing at NASA — check back soon.")
    if not f.get("smap") and not f.get("ecostress") and not waiting:
        g.append("Satellite soil moisture (SMAP) and crop stress (ECOSTRESS) not available for this field yet.")
    if not f.get("grace"):
        g.append("Groundwater trend (GRACE) not included for this location yet.")
    else:
        g.append("GRACE is a regional signal (~300 km) of all water — groundwater, soil and snow — not your well level.")
    if not f.get("soil"):
        g.append("No USDA soil survey data at this point (outside the U.S. or unmapped).")
    return g


def explain(comparison: dict, f: dict) -> dict:
    strategies = comparison["strategies"]
    cur = next((s for s in strategies if s.get("is_current")), None)
    ev = evidence(f)
    gap_list = gaps(f)
    for s in strategies:
        s["why"] = _why(s, cur, comparison["priorities"])
        s["benefits"] = _benefits(s, f)
        wb = _water_benefit(s, cur, f)
        if wb:
            s["benefits"].insert(0, wb)
        s["risks"] = _risks(s, f, comparison["irrigated"])
        s["gaps"] = gap_list
        s["evidence"] = ev
    return comparison


def short_name(s: dict) -> str:
    return s["name"].replace("Your current rotation: ", "")


MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def season_plan(s: dict) -> str:
    """The rotation season by season: "Yr 1: Winter wheat Oct–Jun, double-crop soybeans Jul–Oct · Yr 2: …"."""
    C, CV = crops(), covers()
    parts = []
    for i, cid in enumerate(s["years"]):
        c = C[cid]
        if cid == "fallow":
            seg = "fallow (no crop)"
        elif c["season"] == "perennial":
            seg = f"{c['name']} all year"
        else:
            seg = f"{c['name']} {MONTHS[c['plant_month']]}–{MONTHS[c['harvest_month']]}"
        cv = s.get("covers", {}).get(str(i))
        if cv:
            m = CV[cv]["months"]
            seg += f", {CV[cv]['short']} {MONTHS[m[0]]}–{MONTHS[m[-1]]}"
        parts.append(f"Yr {i + 1}: {seg}")
    return " · ".join(parts)


def chat_summary(comparison: dict, report_url: str | None = None) -> str:
    """Short lines for WhatsApp/SMS: each option with its goal and overall score and its season calendar."""
    lines = []
    for i, s in enumerate(comparison["strategies"]):
        tag = "Today" if s.get("is_current") else f"Option {i}"
        overall = s.get("overall", s["total"])
        lines.append(f"*{tag}: {short_name(s)}* — {s['total']:.0f}/100 for your goals · {overall:.0f}/100 overall")
        if not s.get("is_current"):
            lines.append("  📅 " + season_plan(s))
            lines.append("  " + (s["why"][0] if s["why"] else ""))
            if s.get("generated"):
                lines.append("  🧪 New combination from FarmPartner's search — try it on part of a field first.")
            risks = [r for r in s["risks"] if not r.startswith("A new combination")]
            lines.append("  ⚠️ " + (risks[0] if risks else "Review locally before switching."))
    if report_url:
        lines.append(f"Full comparison with the NASA data: {report_url}")
    lines.append(DISCLAIMER)
    return "\n".join(lines)
