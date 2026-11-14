"""Lean onboarding: 1) location pin or ZIP, 2) confirm the crops we detected from space (+ irrigated?), 3) one goal.

Every handler returns an immediate reply (sent within the webhook) and an optional deferred job for slow work
(fetching NASA data). The deferred job runs in the background and its messages are pushed afterwards.
"""
import re
import threading
from dataclasses import dataclass, field
from typing import Callable

from app import service
from app.data import landcheck
from app.data.geocode import county_name, zip_to_point
from app.db import Farmer, Field, Message, now, session
from app.engine import explain as ex
from app.engine.features import crop_name, rotation_label
from app.engine.knowledge import crops_in_text
from app.prompts import buttons, picker

PRIORITY_PROMPT = picker(
    "Step 3 of 3 — what matters most on this field?", "Choose a goal",
    ("1", "Save water", "Use less irrigation and rain"),
    ("2", "Build soil health", "More organic matter, better structure"),
    ("3", "More income", "Better-paying, reliable crops"),
    ("4", "Less erosion", "Keep the soil covered"),
    ("5", "Forage for livestock", "Feed for cattle"),
)
CROPS_PROMPT = buttons("Step 2 of 3 — is that your rotation?",
                       ("yes irrigated", "Yes, irrigated"), ("yes dryland", "Yes, dryland"),
                       ("different crops", "Different crops"))
IRRIGATION_PROMPT = buttons("Is this field irrigated?", ("irrigated", "Irrigated"), ("dryland", "Dryland"))
NEXT_PROMPT = buttons("What next? Tap a button or just type any question.",
                      ("should I water this week?", "Should I water?"), ("is frost coming?", "Frost coming?"),
                      ("report", "My field report"))
WELCOME = ("👋 Hi! I'm *FarmPartner*, your farm advisor powered by NASA satellite data — free, right here in chat.\n\n"
           "In 3 quick steps I'll compare your crop rotation with options that fit your climate, soil and water:\n"
           "1️⃣ Send your field location — tap 📎 → *Location* and drop the pin on your field (or text your ZIP code)\n"
           "2️⃣ Confirm your crops\n"
           "3️⃣ Pick your main goal\n\n"
           "After that, ask me anything: watering, frost, heat, what to plant next.\n\n"
           "📍 *Step 1 of 3:* send your field location now.")
KNOWN_CROPS = ("corn, soybeans, wheat, grain sorghum (milo), cotton, alfalfa, sunflower, canola, oats, field peas, "
               "proso millet, triticale, and fallow")
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


def _first_insight(field: Field) -> list:
    f = field.features or {}
    c = f.get("climate") or {}
    where = field.place if field.place and not field.place[0].isdigit() and not field.place.startswith("-") else "your field"
    lines = [f"🛰️ Here's what NASA sees at {where}:"]
    if c:
        lines.append(f"• Rain: ~{ex.inch(c['annual_precip_mm'])} in/yr vs ~{ex.inch(c['annual_et0_mm'])} in/yr of evaporation "
                     f"demand → water is the main limit here." if c["water_balance_mm"] < -250 else
                     f"• Rain: ~{ex.inch(c['annual_precip_mm'])} in/yr, about what crops can use.")
        if (c.get("heat_days_recent5") or 0) >= 1:
            lines.append(f"• Days above 95°F: {c['heat_days_recent5']:.0f}/yr lately (was {c['heat_days_early']:.0f}).")
        else:
            lines.append("• Extreme heat (>95°F) is rare here.")
        if c.get("median_last_spring_frost") and c.get("median_first_fall_frost"):
            lines.append(f"• Frost-free season: {c['median_last_spring_frost']} → {c['median_first_fall_frost']}.")
        else:
            lines.append("• Frost is rare here in a typical year.")
    rot = f.get("rotation") or {}
    if rot.get("is_cropland") and field.current_rotation:
        seq = [s for s in rot["sequence"] if s.get("crop")][-5:]
        hist = ", ".join(f"{s['year']} {crop_name(s['crop']).lower()}" for s in seq)
        lines.append(f"\nSatellite crop maps show: {hist}.")
        lines.append(f"Looks like *{rotation_label(field.current_rotation)}*.")
        return ["\n".join(lines), CROPS_PROMPT]
    else:
        outside_us = not (f.get("soil") or (f.get("rotation") or {}).get("sequence"))
        lines.append("\n" + ("Crop-history maps only cover the U.S., so I can't see your past crops from space here. "
                              if outside_us else "I couldn't see crops at that spot from space. ")
                     + "*Step 2 of 3:* what do you grow there? E.g. \"wheat, corn\" — and is it irrigated or dryland?")
    return ["\n".join(lines)]


REPIN = ("🤔 That pin looks like {what}, not a farm field. Please drop the pin right on your field: tap 📎 → "
         "*Location*, move the map until the pin sits on the field, then tap *Send this location*. "
         "(Or text the field's ZIP code.)")


def _set_location(farmer: Farmer, lat: float, lon: float, place: str | None) -> Reply:
    check_pin = place is None  # a ZIP gives the town's centre on purpose; only exact pins are checked
    with session() as db:
        fm = db.get(Farmer, farmer.id)
        prev_state = fm.state
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
            what = landcheck.problem(fld.features or {}) if check_pin else None
            if not what:
                return _first_insight(fld)
            db.delete(fld)
            fm = db.get(Farmer, farmer.id)
            fm.state = "ready" if prev_state == "ready" else "awaiting_location"
            db.commit()
        return [REPIN.format(what=what)]

    return Reply(now=["📡 Got your pin! Checking it's on a field and pulling 20 years of NASA satellite & weather data "
                      "for it — about 30 seconds…"],
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
        return Reply(now=["🔄 Starting over.\n\n📍 *Step 1 of 3:* send your field location — tap 📎 → *Location* and "
                          "drop the pin on your field (or text your ZIP code)."])
    if low in ("help", "?", "menu"):
        return Reply(now=[HELP])
    if low in ("stop", "unsubscribe", "stop all"):
        _set_constraint(farmer, "opted_out", True)
        return Reply(now=["You're unsubscribed — FarmPartner won't message you first again. "
                          "Text HI anytime to come back."])
    if (farmer.constraints or {}).get("opted_out"):
        _set_constraint(farmer, "opted_out", False)  # they wrote to us: they're back
    if low == "not now":
        return Reply(now=["No problem! Message me anytime — just say hi — and I'll look at your field. 🌱"])
    if low in ("get started", "start") and farmer.state in ("new", "awaiting_location"):
        _set_state(farmer, "awaiting_location")
        return Reply(now=[WELCOME])

    state = farmer.state
    if state == "new":
        with session() as db:
            db.get(Farmer, farmer.id).state = "awaiting_location"
            db.commit()
        if lat is not None or zip_to_point(t):
            farmer.state = "awaiting_location"
            return handle(farmer, text, lat, lon)
        return Reply(now=[WELCOME])

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
            said_yes = re.search(r"\b(yes|yep|yeah|right|correct|si|sí|ok|okay)\b", low)
            said_no = (re.search(r"\b(no|nope|wrong|different|not right|incorrect)\b", low)
                       and parse_irrigated(t) is None)  # "dryland, no irrigation" is not a rejection
            if named:
                fld.current_rotation = named if len(named) > 1 else named * 2
            elif said_no or not fld.current_rotation:
                irr_only = parse_irrigated(t)
                if irr_only is not None:  # "dryland" on its own: keep it, ask only for the crops
                    fld.irrigated = irr_only
                    db.commit()
                    return Reply(now=["👍 " + ("Irrigated" if irr_only else "Dryland") + " — noted. Which crops do you "
                                      "grow on this field? Type them in order, e.g. \"corn, soybeans\" or "
                                      "\"wheat, sorghum, fallow\"."])
                db.commit()
                if not said_no and re.search(r"[a-zA-Z]{3}", t):  # they named something we don't know
                    return Reply(now=[f"🌱 I don't have “{t[:40]}” in my crop list yet. I can compare rotations of: "
                                      f"{KNOWN_CROPS}. Which of these do you grow? (Send RESET to start over.)"])
                return Reply(now=["No problem — which crops do you grow on this field? Type them in order, "
                                  "e.g. \"corn, soybeans\" or \"wheat, sorghum, fallow\". Add \"irrigated\" or \"dryland\"."])
            elif not said_yes and parse_irrigated(t) is None:
                return Reply(now=[CROPS_PROMPT])
            irr = parse_irrigated(t)
            if irr is not None:
                fld.irrigated = irr
            rot = rotation_label(fld.current_rotation)
            if fld.irrigated is None:
                fm.state = "awaiting_irrigation"
                db.commit()
                return Reply(now=[f"👍 Got it: {rot}.", IRRIGATION_PROMPT])
            fm.state = "awaiting_priority"
            db.commit()
            irr_txt = " (irrigated)" if fld.irrigated else " (dryland)"
        return Reply(now=[f"👍 Got it: {rot}{irr_txt}.", PRIORITY_PROMPT])

    if state == "awaiting_irrigation":
        irr = parse_irrigated(t)
        if irr is None:
            return Reply(now=[IRRIGATION_PROMPT])
        with session() as db:
            fm = db.get(Farmer, farmer.id)
            fld = service.main_field(fm)
            fld.irrigated, fm.state = irr, "awaiting_priority"
            db.commit()
        return Reply(now=["👍 " + ("Irrigated" if irr else "Dryland") + " — noted.", PRIORITY_PROMPT])

    if state == "awaiting_priority":
        pr = parse_priorities(t)
        if not pr:
            return Reply(now=[PRIORITY_PROMPT])
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
                        + ex.chat_summary(res, service.report_url(fld)), NEXT_PROMPT]
                _spawn(_enrich_ndvi, fld.id)  # slow MODIS pull (~1-2 min); the report picks it up afterwards
                return msgs

        return Reply(now=["🧮 Scoring rotations against your field's climate, soil and goals…"], later=later)
    return None


def _set_constraint(farmer: Farmer, key: str, value) -> None:
    with session() as db:
        fm = db.get(Farmer, farmer.id)
        fm.constraints = {**(fm.constraints or {}), key: value}
        db.commit()
    farmer.constraints = {**(farmer.constraints or {}), key: value}


def _set_state(farmer: Farmer, state: str) -> None:
    with session() as db:
        db.get(Farmer, farmer.id).state = state
        db.commit()
    farmer.state = state


def _spawn(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True).start()


def _enrich_ndvi(field_id: int):
    with session() as db:
        fld = db.get(Field, field_id)
        if fld:
            service.enrich_ndvi(db, fld)
    service.wait_for_satellite(field_id)  # SMAP + ECOSTRESS via AppEEARS (async, minutes)


def record(farmer_id: int, direction: str, body: str, lat=None, lon=None, media_url=None):
    with session() as db:
        db.add(Message(farmer_id=farmer_id, direction=direction, body=body, lat=lat, lon=lon, media_url=media_url))
        if direction == "in":
            db.get(Farmer, farmer_id).last_inbound_at = now()
        db.commit()
