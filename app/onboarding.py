"""Lean onboarding: 1) location pin or ZIP, 2) confirm the crops we detected from space (+ irrigated?), 3) one goal.

Every handler returns an immediate reply (sent within the webhook) and an optional deferred job for slow work
(fetching NASA data). The deferred job runs in the background and its messages are pushed afterwards.
"""
import re
import threading
from dataclasses import dataclass, field
from typing import Callable

from app import service
from app.data.geocode import county_name, zip_to_point
from app.db import Farmer, Field, Message, now, session
from app.engine import explain as ex
from app.engine.features import crop_name, rotation_label
from app.engine.knowledge import crops_in_text

PRIORITY_MENU = ("What matters most on this field? Reply with a number (or two, like '2 4'):\n"
                 "1️⃣ Save water\n2️⃣ Build soil health\n3️⃣ More income\n4️⃣ Less erosion\n5️⃣ Forage for livestock")
PRIORITY_KEYS = {"1": "save_water", "2": "soil_health", "3": "income", "4": "less_erosion", "5": "forage"}
PRIORITY_WORDS = {"water": "save_water", "soil": "soil_health", "income": "income", "money": "income", "profit": "income",
                  "erosion": "less_erosion", "forage": "forage", "cattle": "forage", "livestock": "forage"}
HELP = ("You can ask me things like:\n• \"Should I water this week?\"\n• \"Is frost coming?\"\n"
        "• \"What should I plant after wheat?\"\nOr tell me what you see: \"got 1 inch of rain\", \"saw aphids\".\n"
        "Send REPORT for your field report, RESET to start over.")


@dataclass
class Reply:
    now: list[str] = field(default_factory=list)
    later: Callable[[], list[str]] | None = None


def parse_priorities(text: str) -> list[str]:
    out = [PRIORITY_KEYS[d] for d in re.findall(r"[1-5]", text)]
    for w, k in PRIORITY_WORDS.items():
        if w in text.lower():
            out.append(k)
    return list(dict.fromkeys(out))[:2]


def parse_irrigated(text: str) -> bool | None:
    t = text.lower()
    if re.search(r"\b(dry ?land|rain[- ]?fed|not irrigated|no irrigation|no water)\b", t):
        return False
    if re.search(r"irrigat|pivot|\bwell\b|sprinkler|drip|flood", t):
        return True
    return None


def _first_insight(field: Field) -> str:
    f = field.features or {}
    c = f.get("climate") or {}
    lines = [f"🛰️ Here's what NASA sees at {field.place or 'your field'}:"]
    if c:
        lines.append(f"• Rain: ~{ex.inch(c['annual_precip_mm'])} in/yr vs ~{ex.inch(c['annual_et0_mm'])} in/yr of evaporation "
                     f"demand → water is the main limit here." if c["water_balance_mm"] < -250 else
                     f"• Rain: ~{ex.inch(c['annual_precip_mm'])} in/yr, about what crops can use.")
        if c.get("heat_days_recent5") is not None:
            lines.append(f"• Days above 95°F: {c['heat_days_recent5']:.0f}/yr lately (was {c['heat_days_early']:.0f}).")
        lines.append(f"• Frost-free season: {c['median_last_spring_frost']} → {c['median_first_fall_frost']}.")
    rot = f.get("rotation") or {}
    if rot.get("is_cropland") and field.current_rotation:
        seq = [s for s in rot["sequence"] if s.get("crop")][-5:]
        hist = ", ".join(f"{s['year']} {crop_name(s['crop']).lower()}" for s in seq)
        lines.append(f"\nSatellite crop maps show: {hist}.")
        lines.append(f"Looks like *{rotation_label(field.current_rotation)}*. Is that right?")
        lines.append("Reply YES, or tell me what you grow (e.g. \"corn and soybeans\"). Add \"irrigated\" or \"dryland\".")
    else:
        lines.append("\nI couldn't see crops at that spot from space. What do you grow there, and is it irrigated?")
    return "\n".join(lines)


def _set_location(farmer: Farmer, lat: float, lon: float, place: str | None) -> Reply:
    with session() as db:
        fm = db.get(Farmer, farmer.id)
        place = place or county_name(lat, lon) or f"{lat:.3f}, {lon:.3f}"
        fld = Field(farmer_id=fm.id, lat=lat, lon=lon, place=place)
        db.add(fld)
        fm.state = "awaiting_crops"
        db.commit()
        field_id = fld.id

    def later():
        with session() as db:
            fld = db.get(Field, field_id)
            service.analyze_field(db, fld, with_ndvi=False)
            return [_first_insight(fld)]

    return Reply(now=["📡 Got your field! Pulling 20 years of NASA satellite & weather data for it — about 30 seconds…"],
                 later=later)


def handle(farmer: Farmer, text: str, lat: float | None, lon: float | None) -> Reply | None:
    """Return a Reply while onboarding (or for global commands). Return None when the farmer is ready for the agent."""
    t = (text or "").strip()
    low = t.lower()
    if low in ("reset", "start over", "restart"):
        with session() as db:
            fm = db.get(Farmer, farmer.id)
            for f in list(fm.fields):
                db.delete(f)
            fm.state, fm.priorities, fm.constraints = "awaiting_location", [], {}
            db.commit()
        return Reply(now=["Starting over. 📍 Share your field location (📎 → Location) or text your ZIP code."])
    if low in ("help", "?", "menu"):
        return Reply(now=[HELP])

    state = farmer.state
    if state == "new":
        with session() as db:
            db.get(Farmer, farmer.id).state = "awaiting_location"
            db.commit()
        if lat is not None or zip_to_point(t):
            farmer.state = "awaiting_location"
            return handle(farmer, text, lat, lon)
        return Reply(now=["👋 Hi! I'm FarmPartner. I use NASA satellite data to help you pick crop rotations, "
                          "plan watering and watch for frost and heat — free, right here in chat.\n\n"
                          "📍 To start, share your field location (tap 📎 → Location), or just text your ZIP code."])

    if state == "awaiting_location":
        if lat is not None and lon is not None:
            return _set_location(farmer, lat, lon, None)
        z = zip_to_point(t)
        if z:
            return _set_location(farmer, z[0], z[1], z[2])
        return Reply(now=["I need your field's location to look it up. Tap 📎 → Location and send a pin on the field, "
                          "or text a 5-digit ZIP code."])

    if state == "awaiting_crops":
        with session() as db:
            fm = db.get(Farmer, farmer.id)
            fld = service.main_field(fm)
            if fld is None:
                fm.state = "awaiting_location"
                db.commit()
                return Reply(now=["Please send your field location first (📎 → Location or a ZIP code)."])
            named = crops_in_text(t)
            if named:
                fld.current_rotation = named if len(named) > 1 else named * 2
            elif not fld.current_rotation and not re.search(r"\b(yes|yep|yeah|right|correct|si|sí)\b", low):
                return Reply(now=["Which crops do you grow on this field? E.g. \"wheat, sorghum, fallow\"."])
            irr = parse_irrigated(t)
            if irr is not None:
                fld.irrigated = irr
            fm.state = "awaiting_priority"
            db.commit()
            rot = rotation_label(fld.current_rotation)
            irr_txt = {True: " (irrigated)", False: " (dryland)", None: ""}[fld.irrigated]
        return Reply(now=[f"👍 Got it: {rot}{irr_txt}.\n\n{PRIORITY_MENU}"])

    if state == "awaiting_priority":
        pr = parse_priorities(t)
        if not pr:
            return Reply(now=[PRIORITY_MENU])
        with session() as db:
            fm = db.get(Farmer, farmer.id)
            fm.priorities, fm.state = pr, "ready"
            db.commit()
            farmer_id = fm.id

        def later():
            with session() as db:
                fm = db.get(Farmer, farmer_id)
                fld = service.main_field(fm)
                res = service.recommend(db, fm, fld)
                msgs = ["🌱 I compared your rotation with alternatives that fit your climate:\n\n"
                        + ex.chat_summary(res, service.report_url(fld)), HELP]
                _spawn(_enrich_ndvi, fld.id)  # slow MODIS pull (~1-2 min); the report picks it up afterwards
                return msgs

        return Reply(now=["🧮 Scoring rotations against your field's climate, soil and goals…"], later=later)
    return None


def _spawn(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True).start()


def _enrich_ndvi(field_id: int):
    with session() as db:
        fld = db.get(Field, field_id)
        if fld:
            service.enrich_ndvi(db, fld)


def record(farmer_id: int, direction: str, body: str, lat=None, lon=None, media_url=None):
    with session() as db:
        db.add(Message(farmer_id=farmer_id, direction=direction, body=body, lat=lat, lon=lon, media_url=media_url))
        if direction == "in":
            db.get(Farmer, farmer_id).last_inbound_at = now()
        db.commit()
