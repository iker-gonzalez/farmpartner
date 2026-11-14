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

GOALS = [("1", "Save water", "Use less irrigation and rain"),
         ("2", "Build soil health", "More organic matter, better structure"),
         ("3", "More income", "Better-paying, reliable crops"),
         ("4", "Less erosion", "Keep the soil covered"),
         ("5", "Forage for livestock", "Feed for cattle")]
OTHER_GOAL = ("6", "Something else", "Type your own goal")
PRIORITY_PROMPT = picker("Step 3 of 3 — what matters most on this field? Pick one (you can add more), or just type "
                         "your goals.", "Choose a goal", *GOALS, OTHER_GOAL)
GOAL_TEXT_PROMPT = ("✍️ Tell me your goal in a few words, e.g. \"less fertilizer\", \"keep the soil covered for grazing\" "
                    "or \"a crop I can sell locally\".")
MAX_GOALS = 3


def _more_goals_prompt(chosen: list[str]) -> dict:
    names = " + ".join(_goal_name(k) for k in chosen)
    return buttons(f"Got it: {names}. Add another goal, or compare now?",
                   ("compare now", "Compare now"), ("add goal", "Add another goal"))


def _goal_name(key: str) -> str:
    return next((t for i, t, _ in GOALS if PRIORITY_KEYS[i] == key), key)


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
KNOWN_CROPS = ("corn, soybeans, wheat, barley, oats, grain sorghum (milo), rice, cotton, alfalfa, sunflower, canola, "
               "field peas, dry beans, peanuts, potatoes, tomatoes and other vegetables, sugar beets, sugarcane, "
               "proso millet, triticale, and fallow")
GREETINGS = {"hi", "hello", "hey", "hola", "buenas", "buenos días", "ola", "olá", "bonjour", "salut", "hallo", "ciao",
             "start", "empezar", "comenzar", "こんにちは", "你好", "안녕하세요"}
PRIORITY_KEYS = {"1": "save_water", "2": "soil_health", "3": "income", "4": "less_erosion", "5": "forage"}
PRIORITY_WORDS = {"water": "save_water", "soil": "soil_health", "income": "income", "money": "income", "profit": "income",
                  "erosion": "less_erosion", "forage": "forage", "cattle": "forage", "livestock": "forage",
                  "agua": "save_water", "suelo": "soil_health", "ingreso": "income", "dinero": "income",
                  "ganancia": "income", "erosión": "less_erosion", "forraje": "forage", "ganado": "forage"}
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
    return list(dict.fromkeys(out))[:MAX_GOALS]


def classify_goal(text: str) -> list[str]:
    """Map a goal in the farmer's own words onto the scoring goals (keywords first, then Claude)."""
    pr = parse_priorities(text)
    if pr:
        return pr
    from app import config
    if config.llm_enabled():
        try:
            import json

            from app.agent import claude_client
            resp = claude_client().beta.messages.create(
                model=config.ANTHROPIC_MODEL, max_tokens=200, output_config={"effort": "low"},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                system="Map a farmer's goal (any language) to 1-2 of these crop-rotation scoring goals: save_water, "
                       "soil_health, income, less_erosion, forage. Reply with ONLY a JSON array of keys.",
                messages=[{"role": "user", "content": text[:300]}])
            raw = "".join(b.text for b in resp.content if b.type == "text")
            keys = [k for k in json.loads(re.search(r"\[.*\]", raw, re.S).group(0)) if k in PRIORITY_KEYS.values()]
            if keys:
                return keys[:2]
        except Exception:
            pass
    return ["soil_health"]


def parse_irrigated(text: str) -> bool | None:
    t = text.lower()
    if re.search(r"\b(dry ?land|rain[- ]?fed|not irrigated|no irrigation|no water|secano|sin riego|de temporal|no riego)\b", t):
        return False
    if re.search(r"irrigat|pivot|\bwell\b|sprinkler|drip|flood|riego|regad|irrigad|goteo|aspersi|pivote|pozo", t):
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
    if low in ("reset", "start over", "restart", "reiniciar", "empezar de nuevo", "volver a empezar"):
        with session() as db:
            fm = db.get(Farmer, farmer.id)
            for f in list(fm.fields):
                db.delete(f)
            fm.state, fm.priorities, fm.constraints = "awaiting_location", [], {}
            db.commit()
        return Reply(now=["🔄 Starting over.\n\n📍 *Step 1 of 3:* send your field location — tap 📎 → *Location* and "
                          "drop the pin on your field (or text your ZIP code)."])
    if low in ("help", "?", "menu", "ayuda", "menú"):
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
        if low.strip("!.¡ ") in GREETINGS:
            return Reply(now=[WELCOME])
        if re.fullmatch(r"\d{3,6}", t.replace(" ", "")):
            return Reply(now=[f"I couldn't find ZIP code {t} — ZIP codes only work in the U.S. Anywhere else, send a "
                              "pin (📎 → Location), paste a Google Maps link, or type coordinates like 41.39, 2.17."])
        return Reply(now=["I need your field's location to look it up. Tap 📎 → Location and send a pin on the field, "
                          "paste a Google Maps link, type coordinates (e.g. 38.05, -100.75), or text a U.S. ZIP code."])

    if state == "awaiting_crops":
        with session() as db:
            fm = db.get(Farmer, farmer.id)
            fld = service.main_field(fm)
            if fld is None:
                fm.state = "awaiting_location"
                db.commit()
                return Reply(now=["Please send your field location first (📎 → Location or a ZIP code)."])
            named = crops_in_text(t)
            said_yes = re.search(r"\b(yes|yep|yeah|right|correct|si|sí|ok|okay|correcto|vale|claro|exacto)\b", low)
            said_no = (re.search(r"\b(no|nope|wrong|different|not right|incorrect|incorrecto|otro|otros|distinto)\b", low)
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

    if state in ("awaiting_priority", "awaiting_more_goals", "awaiting_goal_text"):
        return _goals_step(farmer, state, t, low)
    return None


def _save_goals(farmer: Farmer, priorities: list[str], state: str, note: str | None = None) -> None:
    with session() as db:
        fm = db.get(Farmer, farmer.id)
        fm.priorities, fm.state = priorities, state
        if note:
            fm.constraints = {**(fm.constraints or {}), "goal_note": note[:200]}
        db.commit()
    farmer.priorities, farmer.state = priorities, state


def _goals_step(farmer: Farmer, state: str, t: str, low: str) -> Reply:
    chosen = list(farmer.priorities or []) if state != "awaiting_priority" else []
    if state == "awaiting_goal_text" or (state == "awaiting_priority" and not parse_priorities(t)
                                         and len(re.findall(r"\w", t)) >= 6 and low not in ("6", "other")):
        if low in ("6", "something else", "other", "otro", "otra"):
            return Reply(now=[GOAL_TEXT_PROMPT])
        added = classify_goal(t)
        goals = list(dict.fromkeys(chosen + added))[:MAX_GOALS]
        from app.engine.scoring import PRIORITY_LABELS
        names = " + ".join(PRIORITY_LABELS.get(k, k) for k in added)
        return _compare(farmer, goals, note=t, ack=f"📝 Noted your goal: “{t[:120]}” — I'll weigh it as {names}.")
    if low in ("6", "something else", "other", "otro", "otra"):
        _save_goals(farmer, chosen, "awaiting_goal_text")
        return Reply(now=[GOAL_TEXT_PROMPT])
    if state == "awaiting_more_goals":
        if low in ("compare now", "compare", "done", "no", "that's all", "listo", "comparar"):
            return _compare(farmer, chosen or ["soil_health"])
        if low in ("add goal", "add", "another", "more", "otro objetivo"):
            left = [g for g in GOALS if PRIORITY_KEYS[g[0]] not in chosen]
            return Reply(now=[picker("Pick another goal.", "Choose a goal", *left, OTHER_GOAL)])
        added = parse_priorities(t)
        if not added:
            return Reply(now=[_more_goals_prompt(chosen)])
        return _compare(farmer, list(dict.fromkeys(chosen + added))[:MAX_GOALS])
    pr = parse_priorities(t)
    if not pr:
        return Reply(now=[PRIORITY_PROMPT])
    if re.fullmatch(r"[1-5]", t.strip()):  # one tap on the list: offer to add more before comparing
        _save_goals(farmer, pr, "awaiting_more_goals")
        return Reply(now=[_more_goals_prompt(pr)])
    return _compare(farmer, pr)  # typed several goals at once ("1 and 3", "water and soil")


def _compare(farmer: Farmer, priorities: list[str], note: str | None = None, ack: str | None = None) -> Reply:
    _save_goals(farmer, priorities, "ready", note)
    farmer_id = farmer.id

    def later():
        with session() as db:
            fm = db.get(Farmer, farmer_id)
            fld = service.main_field(fm)
            res = service.recommend(db, fm, fld)
            msgs = ["🌱 I compared your rotation with alternatives that fit your climate:\n\n"
                    + ex.chat_summary(res, service.report_url(fld)), NEXT_PROMPT]
            _spawn(_enrich_ndvi, fld.id)  # slow MODIS pull (~1-2 min); the report picks it up afterwards
            return msgs

    return Reply(now=([ack] if ack else []) + ["🧮 Scoring rotations against your field's climate, soil and goals…"],
                 later=later)


def current_step(farmer: Farmer) -> list:
    """Repeat what we're waiting for (used after a language switch)."""
    return {"new": [WELCOME], "awaiting_location": [WELCOME.split("\n\n")[-1]],
            "awaiting_crops": ["Which crops do you grow on this field? Type them in order, e.g. \"corn, soybeans\". "
                               "Add \"irrigated\" or \"dryland\"."],
            "awaiting_irrigation": [IRRIGATION_PROMPT], "awaiting_priority": [PRIORITY_PROMPT],
            "awaiting_more_goals": [_more_goals_prompt(list(farmer.priorities or []))],
            "awaiting_goal_text": [GOAL_TEXT_PROMPT],
            }.get(farmer.state, [NEXT_PROMPT])


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
