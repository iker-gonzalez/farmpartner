"""Lean onboarding: 1) location pin or ZIP, 2) confirm the crops we detected from space (+ irrigated?), 3) one goal.

Every handler returns an immediate reply (sent within the webhook) and an optional deferred job for slow work
(fetching NASA data). The deferred job runs in the background and its messages are pushed afterwards.
"""
import re
import threading
from dataclasses import dataclass, field
from typing import Callable

from app import service
from app.privacy import scrub
from app.data import landcheck
from app.data.geocode import county_name, phone_country, place_to_point, zip_to_point
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
# How to send a pin. On Android, WhatsApp's location screen ignores taps on the map: the pin stays in the middle and
# the map has to be dragged under it. A pin dropped in Google Maps (press and hold) and shared here works on any phone.
PIN_HOWTO = ("Tap 📎 → *Location*, then *drag* the map (tapping it does nothing) until the red pin sits on your field "
             "and tap *Send this location*.\n"
             "Or in Google Maps: press and hold on your field, tap *Share* and send it here.")
# The web chat has no draggable map: its 📎 menu sends the browser's GPS position ("My location").
WEB_PIN_HOWTO = ("Tap 📎 → *My location* if you're standing in the field. Anywhere else: in Google Maps, press and hold "
                 "on your field, tap *Share* → *Copy link* and paste the link here.")
WELCOME = ("👋 Hi! I'm *FarmPartner*, your farm advisor powered by NASA satellite data — free, right here in chat.\n\n"
           "In 3 quick steps I'll compare your crop rotation with options that fit your climate, soil and water:\n"
           "1️⃣ Send your field location (or text your ZIP code)\n"
           "2️⃣ Confirm your crops\n"
           "3️⃣ Pick your main goal\n\n"
           "After that, ask me anything: watering, frost, heat, what to plant next.\n\n"
           "📍 *Step 1 of 3:* send your field location now.\n" + PIN_HOWTO)
KNOWN_CROPS = ("corn, soybeans, wheat, barley, oats, grain sorghum (milo), rice, cotton, alfalfa, sunflower, canola, "
               "field peas, dry beans, peanuts, potatoes, tomatoes and other vegetables, sugar beets, sugarcane, "
               "proso millet, triticale, and fallow")
GREETINGS = {"hi", "hello", "hey", "hola", "buenas", "buenos días", "ola", "olá", "bonjour", "salut", "hallo", "ciao",
             "start", "empezar", "comenzar", "こんにちは", "你好", "안녕하세요"}
TREE_CROPS = re.compile(
    r"\b(pistachios?|pistachos?|pistatxo\w*|olives?|olive trees?|olivos?|olivar\w*|aceitun\w*|olibondo\w*|almonds?|"
    r"almendr\w*|vines?|vineyards?|viñ\w*|vid|uvas?|grapes?|mahats\w*|orchards?|frutal\w*|fruit trees?|"
    r"fruta\w*|citrus|c[ií]tricos|naranj\w*|apples?|manzan\w*|sagar\w*|pears?|peral\w*|peach\w*|melocot\w*|"
    r"cherr\w*|cerez\w*|gerezi\w*|walnuts?|nogal\w*|nueces|hazelnuts?|avellan\w*|avocados?|aguacat\w*|coffee|"
    r"caf[eé]|cacao|cocoa|bananas?|pl[aá]tan\w*|mangos?|kiwis?|tea plantation|t[eé]|lemons?|lim[oó]n\w*)\b", re.I)


# "never planted anything", "it's a new field", "nunca he sembrado nada", "ez dut ezer landatu"
NEVER_CROPPED = re.compile(
    r"\b(never (been )?(planted|grown|sown|farmed|cropped|cultivated)|not (been )?(planted|cropped|farmed) (yet|before)|"
    r"(haven'?t|have not|didn'?t) (planted|grown|sown) (anything|yet|before)|nothing (planted|grown|yet)|new field|"
    r"bare (land|field)|empty (field|plot|land)|no crops?( yet)?$|nunca (he |hemos |se ha |ha )?(plantad|sembrad|cultivad)o|"
    r"no (he|hemos|se ha) (plantado|sembrado|cultivado)|(campo|parcela|terreno) (nuev[oa]|vac[ií][oa]|sin cultivar)|"
    r"sin cultivar|nunca (se ha )?cultivad|bald[ií]o|ezer ez|ez dut ezer)\b|^\W*(nothing|nada|ninguno|none)\W*$", re.I)


def _got_it(current: list[str], label: str, irr_txt: str = "") -> str:
    if current == ["fallow", "fallow"]:
        return (f"👍 Got it: a field that hasn't been cropped yet{irr_txt}. I'll compare starting rotations with "
                "leaving it bare.")
    if current and len(set(current)) == 1 and current[0] != "fallow":
        name = crop_name(current[0]).lower()
        return (f"👍 Got it: you grow {name}{irr_txt}. I'll compare growing it every year with rotations built "
                f"around {name}.")
    return f"👍 Got it: {label}{irr_txt}."


def _orchard(farmer: Farmer, crop: str, irrigated: bool | None) -> "Reply":
    """Tree crops stay for decades: no rotation to compare. Set the field up as an orchard and move on."""
    with session() as db:
        fm = db.get(Farmer, farmer.id)
        fld = service.main_field(fm)
        if fld is not None and irrigated is not None:
            fld.irrigated = irrigated
        fm.constraints = {**(fm.constraints or {}), "perennial_crop": crop}
        fm.state, fm.priorities = "ready", fm.priorities or ["soil_health"]
        db.commit()
    farmer.state = "ready"
    return Reply(now=[f"🌳 {crop.capitalize()}: a permanent tree crop, so a crop rotation doesn't apply — I've set "
                      "up your field as an orchard. I can help with watering, frost and heat alerts, and cover crops "
                      "between the rows. Just ask.", NEXT_PROMPT])


QUESTION = re.compile(r"[?¿？]|^\s*(what|which|how|when|why|where|should|can|could|is|are|do|does|qué|que|cuál|"
                      r"cuáles|cómo|como|cuándo|dónde|por qué|debo|puedo|zer|zein|nola|noiz|non|zergatik|quel|quelle|"
                      r"comment|pourquoi|o que|qual|quais|quando|onde|was|wie|wann|welche|warum|cosa|quale|perché)\b",
                      re.I)
MID_ONBOARDING = ("awaiting_location", "awaiting_crops", "awaiting_irrigation", "awaiting_priority", "awaiting_more_goals",
                  "awaiting_goal_text")


def is_question(text: str) -> bool:
    return bool(QUESTION.search(text or "")) and len((text or "").split()) >= 3


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
    to_agent: bool = False  # not an answer to this step: let Claude reply, then repeat the step


def parse_priorities(text: str) -> list[str]:
    out = [PRIORITY_KEYS[d] for d in re.findall(r"[1-5]", text)]
    for w, k in PRIORITY_WORDS.items():
        if w in text.lower():
            out.append(k)
    return list(dict.fromkeys(out))[:MAX_GOALS]


def crops_any_language(text: str) -> tuple[list[str], list[str]]:
    """Crop ids named in any language ("garia, artoa", "小麦と大豆"), via Claude; plus names it couldn't place."""
    from app import config
    if not config.llm_enabled() or not re.search(r"\w{3}", text):
        return [], []
    try:
        import json

        from app.agent import quick
        from app.engine.knowledge import crops
        ids = {cid: c["name"] for cid, c in crops().items()}
        raw = quick("A farmer (any language) names the crops on a field. Map them, in order, to these crop ids: "
                    + json.dumps(ids) + '. Reply with ONLY JSON: {"crops": [ids in order], "unknown": [real crop '
                    'names that fit none], "closest": {"<unknown crop name>": "<closest crop id by family and season>"}, '
                    '"tree_crop": "English name if they grow a perennial tree/vine crop (orchard, olives, nuts, '
                    'vineyard, fruit), else null"}. Only list real crops; ignore any word that is not a crop name '
                    '(greetings, insults, apologies).', scrub(text)[:300], max_tokens=250)
        import logging
        logging.getLogger(__name__).info("crop mapping (%d chars) -> %s", len(text), raw[:300])  # never log what farmers wrote
        d = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
        tree = d.get("tree_crop")
        unknown = [str(u)[:40] for u in d.get("unknown", [])][:3]
        closest = d.get("closest") or {}
        crops_out = [c for c in d.get("crops", []) if c in ids]
        if not crops_out:  # a real crop we don't model: use the closest one we do (tarwi lupin → field peas)
            for name, cid in closest.items():
                if cid in ids and cid != "fallow":
                    unknown = [f"LIKE:{cid}:{str(name)[:40]}"] + [u for u in unknown if u != name]
        if tree and isinstance(tree, str):
            unknown = ["TREE:" + tree[:40]] + unknown
        return crops_out, unknown
    except Exception:
        import logging
        logging.getLogger(__name__).warning("crop mapping failed (%d chars)", len(text), exc_info=True)
        return [], []


def classify_goal(text: str) -> list[str]:
    """Map a goal in the farmer's own words onto the scoring goals (keywords first, then Claude)."""
    pr = parse_priorities(text)
    if pr:
        return pr
    from app import config
    if config.llm_enabled():
        try:
            import json

            from app.agent import quick
            raw = quick("Map a farmer's goal (any language) to 1-2 of these crop-rotation scoring goals: save_water, "
                        "soil_health, income, less_erosion, forage. Reply with ONLY a JSON array of keys.",
                        scrub(text)[:300], max_tokens=100)
            keys = [k for k in json.loads(re.search(r"\[.*\]", raw, re.S).group(0)) if k in PRIORITY_KEYS.values()]
            if keys:
                return keys[:2]
        except Exception:
            pass
    return ["soil_health"]


def parse_irrigated(text: str) -> bool | None:
    t = text.lower()
    if re.search(r"\b(dry ?land|rain[- ]?fed|not irrigated|no irrigation|no water|secano|sin riego|de temporal|temporal|no riego|lehor\w*|ureztatu gabe\w*)\b", t):
        return False
    if re.search(r"irrigat|pivot|\bwell\b|sprinkler|drip|flood|riego|regad|irrigad|goteo|aspersi|pivote|pozo|ureztat", t):
        return True
    return None


def _country_crops(features: dict, n: int) -> list[str]:
    """The country's top crops by FAOSTAT harvested area, lower-case, without FAOSTAT's qualifiers after a comma or "("."""
    top = ((features or {}).get("country") or {}).get("top") or []
    return [t["item"].split(",")[0].split(" (")[0].lower() for t in top[:n]]


def crop_example(features: dict | None) -> str:
    """Example answer for "what do you grow?": two crops common where the field is, else a generic pair."""
    local = _country_crops(features or {}, 2)
    return ", ".join(local) if len(local) == 2 else "wheat, corn"


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
        c = f.get("country")
        if outside_us and c and c.get("top"):
            names = ", ".join(_country_crops(f, 6))
            lines.append(f"\n🌾 Main crops in {c['country']} (FAOSTAT): {names}.")
        lines.append("\n" + ("Crop-history maps only cover the U.S., so I can't see your past crops from space here. "
                              if outside_us else "I couldn't see crops at that spot from space. ")
                     + f"*Step 2 of 3:* what do you grow there? E.g. \"{crop_example(f)}\" — and is it irrigated or dryland?")
    return ["\n".join(lines)]


REPIN = ("🤔 That pin looks like {what}, not a farm field. Please put the pin right on your field.\n"
         + PIN_HOWTO + "\n(Or text the field's ZIP code.)")


RESTART = "🔄 Starting over.\n\n📍 *Step 1 of 3:* send your field location (or text your ZIP code).\n" + PIN_HOWTO
NEED_LOCATION = ("I need your field's location to look it up.\n" + PIN_HOWTO + "\n"
                 "You can also type coordinates (e.g. 38.05, -100.75) or text a U.S. ZIP code.")


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
            forget_locations(db, fm)
            fm.state, fm.priorities, fm.constraints = "awaiting_location", [], {}
            db.commit()
        return Reply(now=[RESTART])
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
    if state in RESUMABLE and low.strip("!.¡ ") in GREETINGS:
        return Reply(now=[WELCOME_BACK] + _resume_step(farmer))  # a greeting is not an answer to the pending question
    if state == "new":
        with session() as db:
            db.get(Farmer, farmer.id).state = "awaiting_location"
            db.commit()
        if lat is not None or zip_to_point(t, phone_country(farmer.phone)):
            farmer.state = "awaiting_location"
            return handle(farmer, text, lat, lon)
        return Reply(now=[WELCOME])

    if state == "awaiting_location":
        if lat is not None and lon is not None:
            return _set_location(farmer, lat, lon, None)
        z = zip_to_point(t, phone_country(farmer.phone))
        if z:
            return _set_location(farmer, z[0], z[1], z[2])
        if low.strip("!.¡ ") in GREETINGS:
            return Reply(now=[WELCOME])
        if re.search(r"[^\W\d_]{3}", t) and not re.search(r"[?¿؟？]", t):  # a question is never a place name
            try:
                pl = place_to_point(t, farmer.language)
            except Exception:
                pl = None  # a geocoder hiccup must not break the chat: fall through to the location prompt
            if pl:
                r = _set_location(farmer, pl[0], pl[1], pl[2])
                r.now.insert(0, f"📍 I'll use {pl[2]} as an approximate location. For data about your exact field, "
                                "send a pin on it anytime.")
                return r
        if re.fullmatch(r"\d{3,6}", t.replace(" ", "")):
            return Reply(now=[f"I couldn't find ZIP code {t} — ZIP codes only work in the U.S. Anywhere else, send a "
                              "pin (📎 → Location), paste a Google Maps link, or type coordinates like 41.39, 2.17."])
        return Reply(now=[NEED_LOCATION])

    if state == "awaiting_crops":
        with session() as db:
            fm = db.get(Farmer, farmer.id)
            fld = service.main_field(fm)
            if fld is None:
                fm.state = "awaiting_location"
                db.commit()
                return Reply(now=["Please send your field location first (📎 → Location or a ZIP code)."])
            named = crops_in_text(t)
            unknown: list[str] = []
            if not named and NEVER_CROPPED.search(t):
                named = ["fallow", "fallow"]  # nothing grown yet: today's "rotation" is bare ground
            said_no_word = re.search(r"\b(no|nope|wrong|different|incorrect|incorrecto|otro|otros|distinto)\b", low)
            if not named and not said_no_word and re.search(r"\w{3}", t) and not re.fullmatch(
                    r"(dry ?land|irrigated|secano|regad[ií]o|de secano|de regad[ií]o|ureztatua|lehorrekoa)\W*", low) \
                    and not re.fullmatch(r"(yes|yep|yeah|right|correct|si|sí|ok|okay|correcto|vale|claro|exacto)\W*", low):
                named, unknown = crops_any_language(t)
            named = named or []
            tree = TREE_CROPS.search(t)
            if not named and (tree or any(u.startswith("TREE:") for u in unknown)):
                crop = next((u[5:] for u in unknown if u.startswith("TREE:")), None) or tree.group(0).lower()
                db.commit()
                return _orchard(farmer, crop, parse_irrigated(t))
            unknown = [u for u in unknown if not u.startswith("TREE:")]
            note = None
            like = next((u for u in unknown if u.startswith("LIKE:")), None)
            if not named and like:
                _, cid, name = like.split(":", 2)
                named = [cid]
                note = (f"🌱 I don't have {name} in my crop list yet, so I'll compare it as the closest crop I know, "
                        f"*{crop_name(cid)}*. Tell me if that's wrong.")
            unknown = [u for u in unknown if not u.startswith("LIKE:")]
            said_yes = re.search(r"\b(yes|yep|yeah|right|correct|si|sí|ok|okay|correcto|vale|claro|exacto|bai|oui|sim|ja)\b", low)
            said_no = (re.search(r"\b(no|nope|wrong|different|not right|incorrect|incorrecto|otro|otros|distinto)\b", low)
                       and parse_irrigated(t) is None)  # "dryland, no irrigation" is not a rejection
            if named:
                fld.current_rotation = named if len(named) > 1 else named * 2
            elif len(t.split()) >= 4 and not said_yes and not said_no and not unknown:
                # a request or remark, not a crop list ("give me the best dryland rotation"): Claude answers it
                irr_req = parse_irrigated(t)
                if irr_req is not None:
                    fld.irrigated = irr_req
                db.commit()
                return Reply(to_agent=True)
            elif said_no or not fld.current_rotation:
                irr_only = parse_irrigated(t)
                if irr_only is not None:  # "dryland" on its own: keep it, ask only for the crops
                    fld.irrigated = irr_only
                    db.commit()
                    return Reply(now=["👍 " + ("Irrigated" if irr_only else "Dryland") + " — noted. Which crops do you "
                                      "grow on this field? Type them in order, e.g. \"corn, soybeans\" or "
                                      "\"wheat, sorghum, fallow\"."])
                db.commit()
                if not said_no and unknown:  # a real crop we don't know (only Claude-confirmed crop names are echoed)
                    return Reply(now=[f"🌱 I don't have “{', '.join(unknown)}” in my crop list yet. I can compare "
                                      f"rotations of: {KNOWN_CROPS}. Which of these do you grow? (Send RESET to start "
                                      "over.)"])
                if not said_no and re.search(r"\w{3}", t):  # not a crop at all: never repeat it back
                    return Reply(now=[f"🌱 I didn't recognise a crop there. I can compare rotations of: {KNOWN_CROPS}. "
                                      "Which of these do you grow? (Send RESET to start over.)"])
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
                return Reply(now=([note] if note else []) + [_got_it(fld.current_rotation, rot), IRRIGATION_PROMPT])
            fm.state = "awaiting_priority"
            db.commit()
            irr_txt = " (irrigated)" if fld.irrigated else " (dryland)"
        return Reply(now=([note] if note else []) + [_got_it(fld.current_rotation, rot, irr_txt), PRIORITY_PROMPT])

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
        if low in ("compare now", "compare", "done", "no", "that's all", "listo", "comparar") or re.search(
                r"\b(compare|comparar|compara|alderatu|comparer|vergleichen|confrontare)\b", low):
            return _compare(farmer, chosen or ["soil_health"])
        if low in ("add goal", "add", "another", "more") or re.search(
                r"\b(add|another|añadir|anadir|otro objetivo|otra meta|gehitu|ajouter|adicionar|hinzufügen)\b", low):
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


RESUMABLE = ("awaiting_crops", "awaiting_irrigation", "awaiting_priority", "awaiting_more_goals", "awaiting_goal_text")
WELCOME_BACK = "👋 Welcome back! Let's pick up where we left off."


def _resume_step(farmer: Farmer) -> list:
    """The question we were waiting on, asked again in full (with its buttons) when the farmer comes back."""
    if farmer.state == "awaiting_crops":
        with session() as db:
            fld = service.main_field(db.get(Farmer, farmer.id))
            rot = ((fld.features or {}).get("rotation") or {}) if fld else {}
            if fld and rot.get("is_cropland") and fld.current_rotation:
                return [f"Satellite crop maps suggest *{rotation_label(fld.current_rotation)}* on your field.", CROPS_PROMPT]
    return current_step(farmer)


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


def forget_locations(db, farmer: Farmer) -> None:
    """RESET also erases every location the farmer sent from the conversation log: pins, pasted coordinates, map links."""
    from app.data.maplinks import location_from_text
    for m in db.query(Message).filter(Message.farmer_id == farmer.id, Message.direction == "in"):
        body = m.body or ""
        if m.lat is not None or re.search(r"https?://|\d\.\d+\s*[,;]|\d\s*°", body) and _is_location(body, location_from_text):
            m.lat = m.lon = None
            m.body = "[location removed]"


def _is_location(body: str, finder) -> bool:
    from app.data.maplinks import MAP_HOSTS
    if any(h in body for h in MAP_HOSTS if "." in h):  # a pasted map link: no need to resolve it to know
        return True
    try:
        return finder(body) is not None
    except Exception:
        return False


def record(farmer_id: int, direction: str, body: str, lat=None, lon=None, media_url=None):
    with session() as db:
        db.add(Message(farmer_id=farmer_id, direction=direction, body=body, lat=lat, lon=lon, media_url=media_url))
        if direction == "in":
            db.get(Farmer, farmer_id).last_inbound_at = now()
        db.commit()


def static_messages() -> list:
    """FarmPartner's fixed messages (for translation prewarm)."""
    return [WELCOME, HELP, PRIORITY_PROMPT, CROPS_PROMPT, IRRIGATION_PROMPT, NEXT_PROMPT, GOAL_TEXT_PROMPT,
            WELCOME.split("\n\n")[-1],
            RESTART, NEED_LOCATION, WELCOME_BACK,
            "📡 Got your pin! Checking it's on a field and pulling 20 years of NASA satellite & weather data for it — "
            "about 30 seconds…",
            "🧮 Scoring rotations against your field's climate, soil and goals…",
            "🌱 I compared your rotation with alternatives that fit your climate:",
            "Which crops do you grow on this field? Type them in order, e.g. \"corn, soybeans\". Add \"irrigated\" or "
            "\"dryland\".",
            "No problem — which crops do you grow on this field? Type them in order, e.g. \"corn, soybeans\" or "
            "\"wheat, sorghum, fallow\". Add \"irrigated\" or \"dryland\".",
            "📷 Got it — taking a look…", "I heard:",
            "👍 Got it — I'll use metric units: mm of rain and water, °C, hectares.",
            "Pick another goal."]


def button_ids() -> set[str]:
    """Ids of every tap-able choice: a tap sends the id (an English phrase), which must not count as language."""
    ids = {"compare now", "add goal", "get started", "not now", "report", "6"}
    for m in (PRIORITY_PROMPT, CROPS_PROMPT, IRRIGATION_PROMPT, NEXT_PROMPT):
        ids |= {b["id"] for b in m.get("buttons", [])} | {i["id"] for i in (m.get("list") or {}).get("items", [])}
    return {i.lower() for i in ids}
