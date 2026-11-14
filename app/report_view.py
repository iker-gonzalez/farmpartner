"""Numbers the field report page shows beyond the raw features: the bottom line, the biggest gain over today,
the worst monthly water gap, the longest low-cover stretch from NDVI, and colours for the crop timeline.

Everything is derived from the stored features and recommendation; nothing here is invented.
"""
import datetime as dt

from app.engine.explain import SCORE_LABELS, inch, short_name
from app.engine.features import crop_name
from app.engine.knowledge import covers

BARE_NDVI = 0.25
CROP_COLORS = {
    "winter_wheat": "#d9b25f", "spring_wheat": "#d9b25f", "wheat": "#d9b25f", "sorghum": "#c2703d",
    "fallow": "#4a3b2e", "canola": "#efe04d", "corn": "#e8c547", "soybeans": "#7fae4a", "soybean": "#7fae4a",
    "cotton": "#e9e4d8", "alfalfa": "#5fae3e", "sunflower": "#f2b632", "barley": "#c9a35a", "oats": "#cdb67a",
    "peanuts": "#b98a5a", "rice": "#9cc46b", "dry_beans": "#a5764a", "millet": "#c7a46a",
}
DARK_TEXT = {"fallow"}
MONTH_FULL = {"Jan": "January", "Feb": "February", "Mar": "March", "Apr": "April", "May": "May", "Jun": "June",
              "Jul": "July", "Aug": "August", "Sep": "September", "Oct": "October", "Nov": "November", "Dec": "December"}
MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def crop_color(cid: str | None) -> str:
    return CROP_COLORS.get(cid or "", "#8b8b95")


def crop_text(cid: str | None) -> str:
    return "#d8c3a8" if cid in DARK_TEXT else "#0a0a0b"


def _strategies(rec: dict | None):
    ss = (rec or {}).get("strategies") or []
    cur = next((s for s in ss if s.get("is_current")), None)
    alts = [s for s in ss if not s.get("is_current")]
    return cur, (alts[0] if alts else None)


def _top_gain(best: dict, cur: dict | None):
    if not (best and cur):
        return None
    d, k = max((best["scores"][k] - cur["scores"][k], k) for k in best["scores"])
    return {"label": SCORE_LABELS.get(k, k), "delta": d} if d > 0 else None


def _water(climate: dict):
    years = climate.get("years") or ""
    months = climate.get("monthly") or []
    rows = [{"m": m["month"], "rain": inch(m["precip_mm"]) or 0, "et": inch(m["et0_mm"]) or 0} for m in months]
    if not rows:
        return None
    top = max(max(r["rain"], r["et"]) for r in rows) or 1
    for r in rows:
        r["rain_pct"] = round(100 * r["rain"] / top)
        r["et_pct"] = round(100 * r["et"] / top)
    worst = max(rows, key=lambda r: r["et"] - r["rain"])
    gap = round(worst["et"] - worst["rain"], 1)
    for r in rows:
        d = round(r["rain"] - r["et"], 1)
        r["tip"] = {"t": MONTH_FULL.get(r["m"], r["m"]), "s": f"{years} average".strip(),
                    "r": [["Rain", f'{r["rain"]} in', "rain"], ["Crop demand (ET₀)", f'{r["et"]} in', "et"],
                          ["Shortfall" if d < 0 else "Surplus", f"{d:+.1f} in".replace("-", "−"), "et" if d < 0 else "lime"]],
                    "n": "Biggest shortfall of the year" if r is worst and gap > 0 else None}
    return {"rows": rows, "worst": worst if gap > 0 else None, "gap": gap}


def _month_label(d: str) -> str:
    try:
        x = dt.date.fromisoformat(d)
        return f"{MONTH_ABBR[x.month - 1]} {x.year}"
    except ValueError:
        return d


def _day_label(d: str) -> str:
    try:
        x = dt.date.fromisoformat(d)
        return f"{MONTH_ABBR[x.month - 1]} {x.day}, {x.year}"
    except ValueError:
        return d


def _ndvi_tip(p: dict) -> dict:
    v = p["ndvi"]
    if v < BARE_NDVI:
        state, cls, note = "Near-bare soil", "bare", "Bare stretches are when a cover crop can protect the soil."
    elif v < 0.4:
        state, cls, note = "Sparse cover", "green", None
    elif v < 0.6:
        state, cls, note = "Green cover", "green", None
    else:
        state, cls, note = "Dense, healthy cover", "lime", None
    return {"t": _day_label(p["date"]), "s": "MODIS 16-day composite",
            "r": [["NDVI", f"{v:.2f}", cls], ["Ground", state, cls]], "n": note}


def _ndvi(ndvi: dict):
    series = [p for p in (ndvi.get("series") or []) if p.get("ndvi") is not None]
    if not series:
        return None
    pts = [{"date": p["date"], "v": p["ndvi"], "pct": round(min(1.0, max(0.0, p["ndvi"])) * 100),
            "low": p["ndvi"] < BARE_NDVI, "tip": _ndvi_tip(p)} for p in series]
    best, run = None, None
    for i, p in enumerate(pts):
        if p["low"]:
            run = (run[0], i) if run else (i, i)
            if not best or run[1] - run[0] > best[1] - best[0]:
                best = run
        else:
            run = None
    low = None
    if best:
        a, b = pts[best[0]]["date"], pts[best[1]]["date"]
        try:
            days = (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days + 16  # MODIS composites are 16-day
        except ValueError:
            days = (best[1] - best[0] + 1) * 16
        months = round(days / 30.4)
        if months >= 1:
            low = {"from": _month_label(a), "to": _month_label(b), "months": months}
    step = max(1, (len(pts) - 1) // 4)
    ticks = [_month_label(pts[i]["date"]) for i in range(0, len(pts), step)][:5]
    return {"points": pts, "low": low, "ticks": ticks, "first": _month_label(pts[0]["date"]),
            "last": _month_label(pts[-1]["date"])}


def _timeline(s: dict):
    cv = covers()
    out = []
    for i, cid in enumerate(s["years"]):
        cover = (s.get("covers") or {}).get(str(i))
        cover_name = cv.get(cover, {}).get("name") if cover else None
        name = crop_name(cid).replace(" (no crop)", "")
        rows = [["Main crop", "None — fallow" if cid == "fallow" else name, None]]
        if cover_name:
            rows.append(["Cover crop", cover_name, "green"])
        out.append({"name": name, "color": crop_color(cid), "text": crop_text(cid), "cover": cover_name,
                    "tip": {"t": f"Year {i + 1} of {len(s['years'])}", "r": rows,
                            "n": (cv.get(cover, {}).get("note") if cover else None)}})
    return out


def _bars(s: dict, cur: dict | None, best: dict | None) -> list[dict]:
    """Score bars with a tooltip comparing this plan to today (or today to the best match)."""
    ref, ref_name = (best, "Best match") if s.get("is_current") else (cur, "Today")
    out = []
    for k, v in s["scores"].items():
        rows = [["This plan" if not s.get("is_current") else "Today", f"{v} / 100", "lime"]]
        if ref and k in ref["scores"]:
            d = v - ref["scores"][k]
            rows.append([ref_name, f'{ref["scores"][k]} / 100', None])
            rows.append(["Difference", (f"{d:+d}" if d else "same").replace("-", "−"),
                         "lime" if d > 0 else ("et" if d < 0 else None)])
        out.append({"k": k, "v": v, "tip": {"t": SCORE_LABELS.get(k, k).capitalize(), "r": rows}})
    return out


def build(field, rec: dict | None, f: dict) -> dict:
    cur, best = _strategies(rec)
    history = []
    for h in field.history or []:
        name = crop_name(h.crop).replace(" (no crop)", "") if h.crop else (h.category or "?")
        rows = [["Satellite class", h.category or "—", None], ["Read as", name, None]]
        history.append({"year": h.year, "name": name, "color": crop_color(h.crop), "text": crop_text(h.crop),
                        "tip": {"t": str(h.year), "s": "Told us by you" if h.source == "farmer" else "USDA Cropland Data Layer · 30 m",
                                "r": rows}})
    fallow_years = sum(1 for h in (field.history or []) if h.crop == "fallow")
    return {
        "best": best, "cur": cur, "gain": _top_gain(best, cur),
        "best_name": short_name(best) if best else None,
        "water": _water(f.get("climate") or {}),
        "ndvi": _ndvi(f.get("ndvi") or {}),
        "strats": [dict(s, timeline=_timeline(s), bars=_bars(s, cur, best), is_best=s is best) for s in (rec or {}).get("strategies", [])],
        "history": history, "fallow_years": fallow_years,
        "elevation": f.get("elevation_m"),
    }
